"""
PrimeFX Chronological YouTube Curriculum Scraper & Lesson Extractor (Oldest -> Latest).
1. Discovers all videos from PrimeFX channel via yt-dlp --flat-playlist --dump-json.
2. Sorts strictly in chronological order (oldest upload -> latest upload).
3. Fetches transcripts via youtube-transcript-api (with yt-dlp VTT auto-sub fallback).
4. Feeds transcripts chronologically through Gemini (Pro -> Flash cascade) with strict
   Pydantic schemas (PrimeFxVideoLessonSchema) to separate Winning Blueprints from
   Risk Flaws & Failure Modes.
5. Compiles master playbook JSON & Markdown curriculum files.
"""

import concurrent.futures
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# Allow direct execution: python src/training/primefx_trainer.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    YTT_AVAILABLE = True
except ImportError:
    YTT_AVAILABLE = False


class PrimeFxVideoLessonSchema(BaseModel):
    video_id: str = Field(description="YouTube video ID")
    video_title: str = Field(description="Original video title")
    upload_date: str = Field(description="ISO YYYY-MM-DD upload date")
    video_type: str = Field(description="TUTORIAL, LIVE_TRADE, or RECAP")
    instruments_covered: List[str] = Field(description="Deriv synthetic or FX instruments discussed")
    core_technical_fundamental_logic: str = Field(description="How HTF bias, external/internal liquidity, and candle ranges are read")
    liquidity_play_mechanics: str = Field(description="Exact liquidity raid/sweep, inducement (IDM) traps, and displacement confirmation")
    poi_selection_rules: str = Field(description="How OB, FVG, IFVG, Breaker, 50% Equilibrium/OTE POIs are refined H4->H1->M15->M5")
    winning_trade_blueprints: List[str] = Field(description="Exact confluence rules driving big winning setups")
    failure_modes_and_risk_flaws: List[str] = Field(description="Losses, missed trades, oversized lots, premature POI entries, or psychology/greed flaws")


class PrimeFxCurriculumBatchSchema(BaseModel):
    chronological_theme_evolution: str = Field(description="How PrimeFX strategy evolved across this chronological batch")
    lessons: List[PrimeFxVideoLessonSchema]


class PrimeFxMasterPlaybookSchema(BaseModel):
    mentor_name: str
    framework_summary: str
    core_htf_bias_rules: List[str]
    liquidity_sweep_and_inducement_rules: List[str]
    poi_hierarchy_and_refinement: List[str]
    deriv_synthetic_jump_vix_adaptation: List[str]
    winning_trade_checklist: List[str]
    critical_flaws_to_counter_with_risk_shield: List[str]


class PrimeFxTrainer:
    MODEL_CASCADE = [
        "gemini-2.5-pro",
        "gemini-3.1-pro-preview",
        "gemini-2.5-flash",
        "gemini-3-flash-preview",
        "gemini-3.5-flash",
        "gemini-2.5-flash-lite",
    ]

    def __init__(self, sources_path: str = "config/training_sources.json", api_key: Optional[str] = None):
        self.sources_path = sources_path
        self.config = self._load_config()
        outputs = self.config.get("outputs", {})
        self.catalog_path = outputs.get("catalog_path", "knowledge_base/primefx_video_catalog.json")
        self.curriculum_report_path = outputs.get("curriculum_report_path", "reports/PRIMEFX_VIDEO_CURRICULUM.md")
        self.master_playbook_json = outputs.get("master_playbook_json", "knowledge_base/primefx_master_playbook.json")
        self.logic_and_flaws_md = outputs.get("logic_and_flaws_md", "knowledge_base/PRIMEFX_LOGIC_AND_FLAWS.md")
        self.transcripts_dir = outputs.get("transcripts_cache_dir", "knowledge_base/primefx_transcripts")

        self._load_env()
        self.api_key = api_key if api_key is not None else (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        self.client = None
        self._disabled_models = set()
        if GENAI_AVAILABLE and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Could not init Gemini client in PrimeFxTrainer: {e}")

    @staticmethod
    def _load_env():
        env_path = os.path.join(PROJECT_ROOT, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() not in os.environ or not os.environ[k.strip()]:
                            os.environ[k.strip()] = v.strip().strip("'\"")

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.sources_path):
            with open(self.sources_path, "r") as f:
                return json.load(f)
        return {
            "channel_urls": ["https://www.youtube.com/@Prime_fx/videos"],
            "fallback_search_query": "PrimeFx Deriv V75 Jump Index ICT",
            "outputs": {}
        }

    @staticmethod
    def classify_video_metadata(title: str) -> Dict[str, Any]:
        """Deterministically classifies video type and Deriv instruments from title."""
        t_low = (title or "").lower()
        # Video type classification
        if any(k in t_low for k in ["explained", "what is", "master", "teach you", "how to", "breakdown", "top down", "guide", "reference point", "inefficiencies", "multi timeframe", "scaling in", "lesson", "episode"]):
            v_type = "TUTORIAL"
        elif any(k in t_low for k in ["fail", "blew", "blown", "recap", "brutal reality", "lesson", "improve", "fix this", "focus", "guard your mind", "talk"]):
            v_type = "RECAP"
        else:
            v_type = "LIVE_TRADE"

        instruments: List[str] = []
        patterns = [
            (r"\bjump\s*10\b|\bjd\s*10\b", "Jump 10 (JD10)"),
            (r"\bjump\s*25\b|\bjd\s*25\b", "Jump 25 (JD25)"),
            (r"\bjump\s*50\b|\bjd\s*50\b", "Jump 50 (JD50)"),
            (r"\bjump\s*75\b|\bjd\s*75\b", "Jump 75 (JD75)"),
            (r"\bjump\s*100\b|\bjd\s*100\b", "Jump 100 (JD100)"),
            (r"\bvix\s*75\s*1?s\b|\b75s\b", "Volatility 75 (1s) (1HZ75V)"),
            (r"\bvix\s*75\b|\bv\s*75\b", "Volatility 75 (R_75)"),
            (r"\bvix\s*50\b|\bv\s*50\b", "Volatility 50 (1HZ50V/R_50)"),
            (r"\bvix\s*25\s*1?s\b|\b25s\b", "Volatility 25 (1s) (1HZ25V)"),
            (r"\bvix\s*25\b|\bv\s*25\b", "Volatility 25 (1HZ25V/R_25)"),
            (r"\bvix\s*10\s*1?s\b|\b10s\b", "Volatility 10 (1s) (1HZ10V)"),
            (r"\bvix\s*100\s*1?s\b|\b100s\b", "Volatility 100 (1s) (1HZ100V)"),
            (r"\bvix\s*100\b", "Volatility 100 (R_100)"),
            (r"\bstep\s*index\b", "Step Index (STEPINDEX)"),
            (r"\bboom\b|\bb500\b", "Boom Indices"),
            (r"\bcrash\b|\bc300\b", "Crash Indices"),
            (r"\bgold\b|\bxau\b", "XAUUSD / Gold"),
        ]
        for pat, label in patterns:
            if re.search(pat, t_low):
                if label not in instruments:
                    instruments.append(label)
        if not instruments and ("deriv" in t_low or "vix" in t_low or "smc" in t_low):
            instruments.append("Deriv Synthetic Indices (General)")
        elif not instruments:
            instruments.append("Multi-Market / ICT Concepts")

        return {"video_type": v_type, "instruments": instruments}

    @staticmethod
    def sort_videos_chronologically(videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sorts videos strictly from OLDEST to LATEST upload.
        Uses (upload_timestamp ASC, -playlist_index ASC) as deterministic tiebreaker.
        """
        def _sort_key(v: Dict[str, Any]):
            ts = v.get("timestamp") or 0
            ud = v.get("upload_date") or ""
            if not ts and ud and len(ud) >= 8:
                try:
                    dt = datetime.strptime(ud[:10].replace("-", ""), "%Y%m%d").replace(tzinfo=timezone.utc)
                    ts = int(dt.timestamp())
                except Exception:
                    ts = 0
            # yt-dlp returns playlist_index=1 for newest and N for oldest -> reverse tiebreak
            p_idx = int(v.get("playlist_index") or 0)
            return (int(ts), -p_idx, v.get("id", ""))

        sorted_list = sorted(videos, key=_sort_key)
        for idx, v in enumerate(sorted_list, start=1):
            v["chronological_rank"] = idx
        return sorted_list

    def discover_videos(self) -> List[Dict[str, Any]]:
        """Discovers all videos across configured PrimeFX YouTube channels sorted Oldest -> Latest."""
        raw_videos: Dict[str, Dict[str, Any]] = {}
        urls = list(self.config.get("channel_urls", []))

        for channel_url in urls:
            try:
                cmd = [
                    "yt-dlp",
                    "--flat-playlist",
                    "--extractor-args", "youtubetab:approximate_date",
                    "--dump-single-json",
                    channel_url,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if proc.returncode != 0 or not proc.stdout.strip():
                    continue
                payload = json.loads(proc.stdout)
                entries = payload.get("entries") or []
                channel_name = payload.get("channel") or payload.get("uploader") or channel_url
                # Skip tiny non-Deriv generic impersonator channels if main channel (@Prime_fx) yields rich catalog
                if "@PrimeFXInsights" in channel_url and len(entries) <= 5 and len(raw_videos) >= 20:
                    continue
                for entry in entries:
                    vid = entry.get("id")
                    if not vid:
                        continue
                    ts = entry.get("timestamp")
                    upload_date = (
                        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        if ts else (entry.get("upload_date") or "Unknown")
                    )
                    if len(upload_date) == 8 and upload_date.isdigit():
                        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
                    title = entry.get("title") or ""
                    meta = self.classify_video_metadata(title)
                    raw_videos[vid] = {
                        "id": vid,
                        "title": title,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "channel": entry.get("channel") or channel_name,
                        "timestamp": ts or 0,
                        "upload_date": upload_date,
                        "duration_string": entry.get("duration_string") or "",
                        "duration_seconds": entry.get("duration") or 0,
                        "view_count": entry.get("view_count") or 0,
                        "playlist_index": entry.get("playlist_index") or 0,
                        "video_type": meta["video_type"],
                        "instruments": meta["instruments"],
                    }
            except Exception as e:
                logger.warning(f"yt-dlp channel discovery failed for {channel_url}: {e}")

        # Fallback search discovery if primary channels returned empty
        if not raw_videos:
            q = self.config.get("fallback_search_query", "PrimeFx Deriv V75 Jump Index ICT")
            try:
                cmd = ["yt-dlp", "--flat-playlist", "--dump-json", f"ytsearch25:{q}"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                for line in proc.stdout.splitlines():
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    vid = entry.get("id")
                    if vid and vid not in raw_videos:
                        title = entry.get("title") or ""
                        meta = self.classify_video_metadata(title)
                        raw_videos[vid] = {
                            "id": vid,
                            "title": title,
                            "url": f"https://www.youtube.com/watch?v={vid}",
                            "channel": entry.get("channel") or "Prime_Fx",
                            "timestamp": entry.get("timestamp") or 0,
                            "upload_date": entry.get("upload_date") or "Unknown",
                            "duration_string": entry.get("duration_string") or "",
                            "view_count": entry.get("view_count") or 0,
                            "playlist_index": entry.get("playlist_index") or 0,
                            "video_type": meta["video_type"],
                            "instruments": meta["instruments"],
                        }
            except Exception as e:
                logger.warning(f"Fallback YouTube search failed: {e}")

        sorted_videos = self.sort_videos_chronologically(list(raw_videos.values()))
        logger.info(f"Discovered {len(sorted_videos)} PrimeFX videos (sorted Oldest #1 -> Latest #{len(sorted_videos)}).")
        return sorted_videos

    def fetch_transcript(self, video_id: str) -> str:
        """Fetches transcript from cache, youtube-transcript-api, or yt-dlp auto-subs."""
        os.makedirs(self.transcripts_dir, exist_ok=True)
        cache_file = os.path.join(self.transcripts_dir, f"{video_id}.txt")
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 20:
            with open(cache_file, "r") as f:
                return f.read()

        text = ""
        if YTT_AVAILABLE:
            try:
                api = YouTubeTranscriptApi() if hasattr(YouTubeTranscriptApi, "list") else YouTubeTranscriptApi
                if hasattr(api, "list"):
                    tlist = api.list(video_id)
                    langs = [t.language_code for t in tlist]
                    pref = next((l for l in ["en", "en-US", "en-GB"] if l in langs), langs[0] if langs else None)
                    if pref:
                        fetched = tlist.find_transcript([pref]).fetch()
                        text = " ".join(x.text if hasattr(x, "text") else x["text"] for x in fetched)
                else:
                    fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "hi"])
                    text = " ".join(x["text"] for x in fetched)
            except Exception:
                text = ""

        # yt-dlp auto-sub fallback
        if not text:
            try:
                tmp_tpl = os.path.join(self.transcripts_dir, f"{video_id}.%(ext)s")
                subprocess.run(
                    ["yt-dlp", "--write-auto-sub", "--sub-lang", "en,en-US", "--skip-download", "-o", tmp_tpl, f"https://www.youtube.com/watch?v={video_id}"],
                    capture_output=True, text=True, timeout=20
                )
                for fn in os.listdir(self.transcripts_dir):
                    if fn.startswith(video_id) and fn.endswith(".vtt"):
                        vtt_path = os.path.join(self.transcripts_dir, fn)
                        with open(vtt_path, "r") as vf:
                            lines = [
                                ln.strip() for ln in vf.read().splitlines()
                                if ln.strip() and "-->" not in ln and not ln.startswith("WEBVTT") and not ln.startswith("Kind:") and not ln.startswith("Language:")
                            ]
                        # Deduplicate consecutive VTT caption lines
                        dedup = []
                        for ln in lines:
                            clean = re.sub(r"<[^>]+>", "", ln).strip()
                            if clean and (not dedup or dedup[-1] != clean):
                                dedup.append(clean)
                        text = " ".join(dedup)
                        os.remove(vtt_path)
                        break
            except Exception:
                text = ""

        text = re.sub(r"\s+", " ", text).strip()
        if text:
            with open(cache_file, "w") as f:
                f.write(text)
        return text

    def fetch_all_transcripts(self, videos: List[Dict[str, Any]], max_workers: int = 8) -> Dict[str, str]:
        """Fetches transcripts for all videos concurrently while returning map keyed by video_id."""
        results: Dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_map = {ex.submit(self.fetch_transcript, v["id"]): v["id"] for v in videos}
            for fut in concurrent.futures.as_completed(future_map):
                vid = future_map[fut]
                try:
                    results[vid] = fut.result()
                except Exception:
                    results[vid] = ""
        covered = sum(1 for val in results.values() if len(val) > 50)
        logger.info(f"Transcript extraction complete: {covered}/{len(videos)} videos have transcripts.")
        return results

    def _call_gemini_structured(self, prompt: str, schema_cls: Any) -> Optional[Dict[str, Any]]:
        """Executes structured extraction through Gemini Pro->Flash cascade with clean fallback."""
        if not self.client:
            return None
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_cls,
            temperature=0.2,
        )
        for model_name in self.MODEL_CASCADE:
            if model_name in self._disabled_models:
                continue
            for attempt in range(2):
                try:
                    resp = self.client.models.generate_content(
                        model=model_name,
                        contents=[prompt],
                        config=config
                    )
                    parsed = json.loads(resp.text)
                    parsed["_ai_model"] = model_name
                    return parsed
                except Exception as e:
                    err_s = str(e)
                    logger.warning(f"Trainer Gemini [{model_name}] failed: {type(e).__name__}: {err_s[:160]}")
                    if ("404" in err_s and "NOT_FOUND" in err_s) or ("limit: 0" in err_s) or ("429" in err_s and "PerDay" in err_s):
                        self._disabled_models.add(model_name)
                        break
                    time.sleep(2.0 * (attempt + 1))
        return None

    def extract_single_video_lesson(self, video: Dict[str, Any], transcript: str) -> Dict[str, Any]:
        """Feeds a single video transcript strictly adhering to PrimeFxVideoLessonSchema."""
        prompt = f"""
Analyze this PrimeFX YouTube trading video cleanly using ONLY pure ICT / Price-Action / Liquidity concepts (NO orderflow/volume assumptions).
Video Rank: #{video.get('chronological_rank')} | Upload Date: {video.get('upload_date')} | ID: {video.get('id')}
Title: {video.get('title')}
Video Type: {video.get('video_type')} | Instruments: {', '.join(video.get('instruments', []))}

Transcript Excerpt:
{transcript[:12000] if transcript else '[No spoken transcript; infer carefully from title & Deriv ICT methodology]'}

Return strict JSON matching PrimeFxVideoLessonSchema, explicitly separating WHAT WINS vs WHERE HE FAILS / TAKES EXCESSIVE RISK.
"""
        parsed = self._call_gemini_structured(prompt, PrimeFxVideoLessonSchema)
        if parsed:
            return parsed
        return self._heuristic_lesson(video, transcript)

    def _heuristic_lesson(self, video: Dict[str, Any], transcript: str) -> Dict[str, Any]:
        title = video.get("title", "")
        v_type = video.get("video_type", "LIVE_TRADE")
        t_low = (transcript + " " + title).lower()
        return {
            "video_id": video["id"],
            "video_title": title,
            "upload_date": video.get("upload_date", "Unknown"),
            "video_type": v_type,
            "instruments_covered": video.get("instruments", []),
            "core_technical_fundamental_logic": "Top-down HTF Daily/H4 external bias paired with clean price-action structure shifts without news catalysts on Deriv algorithms.",
            "liquidity_play_mechanics": "Wait for internal inducement (IDM) or prior high/low sweep (wick close back inside range) before displacement into opposing liquidity.",
            "poi_selection_rules": "Refine H4 Order Blocks / 3-candle FVGs / Breaker Blocks into H1/M15 OTE (0.618-0.786) discount/premium zones.",
            "winning_trade_blueprints": [
                "Liquidity raid + M15 Market Structure Shift (body close) + unmitigated OB/FVG retest aligned with H4 bias."
            ],
            "failure_modes_and_risk_flaws": [
                "Small-account over-leveraging / full-margin flipping risk on VIX/Jump indices"
                if any(k in t_low for k in ["full margin", "blew", "blown", "fail", "10usd", "11usd", "14usd", "20usd"])
                else "Premature entry at POI before inducement is swept or widening stops during Jump spikes."
            ],
        }

    def extract_curriculum_chronologically(
        self,
        videos: List[Dict[str, Any]],
        transcripts: Dict[str, str],
        batch_size: int = 12,
        per_video: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Digests videos in strict chronological order (Oldest #1 -> Latest #N).
        Defaults to chronological batches of 12 transcripts so the entire 133-video library
        completes reliably across Gemini quotas using PrimeFxCurriculumBatchSchema.
        """
        lessons: List[Dict[str, Any]] = []
        if per_video:
            for v in videos:
                lessons.append(self.extract_single_video_lesson(v, transcripts.get(v["id"], "")))
            return lessons

        for start in range(0, len(videos), batch_size):
            chunk = videos[start:start + batch_size]
            chunk_blocks = []
            for v in chunk:
                tr = (transcripts.get(v["id"]) or "")[:2400]
                chunk_blocks.append(
                    f"--- VIDEO #{v['chronological_rank']} ({v['upload_date']}) | ID={v['id']} | TYPE={v['video_type']} | "
                    f"INSTRUMENTS={v['instruments']} | TITLE={v['title']} ---\nTRANSCRIPT: {tr}"
                )
            prompt = f"""
You are extracting a quantitative, chronological trading curriculum from PrimeFX's YouTube archive
in strict OLDEST-TO-LATEST order (Videos #{chunk[0]['chronological_rank']} through #{chunk[-1]['chronological_rank']}).

Remember: Deriv Synthetic Indices have ZERO real-world orderflow, DOM, or volume profile. Extract PURE Price Action,
HTF Bias, Liquidity Raids/Sweeps (BSL/SSL/EQH/EQL/PDH/PDL), Inducement (IDM) traps, POIs (OB, FVG, IFVG, Breaker Blocks, OTE),
and explicitly separate:
1. WINNING TRADE BLUEPRINTS (why his profitable setups succeed with high R:R)
2. FAILURE MODES & RISK FLAWS (where he blows accounts, uses full-margin lot sizing, enters pre-inducement, revenge trades, or mismanages exits).

Output ONE PrimeFxVideoLessonSchema entry for EVERY video in this batch (all {len(chunk)} video IDs preserved accurately).

VIDEOS:
{chr(10).join(chunk_blocks)}
"""
            logger.info(f"Extracting PrimeFX lessons batch #{chunk[0]['chronological_rank']}-#{chunk[-1]['chronological_rank']} via Gemini...")
            parsed = self._call_gemini_structured(prompt, PrimeFxCurriculumBatchSchema)
            if parsed and isinstance(parsed.get("lessons"), list):
                by_id = {item["video_id"]: item for item in parsed["lessons"] if "video_id" in item}
                for v in chunk:
                    if v["id"] in by_id:
                        item = by_id[v["id"]]
                        item["_ai_model"] = parsed.get("_ai_model", "gemini")
                        lessons.append(item)
                    else:
                        lessons.append(self._heuristic_lesson(v, transcripts.get(v["id"], "")))
            else:
                for v in chunk:
                    lessons.append(self._heuristic_lesson(v, transcripts.get(v["id"], "")))
            time.sleep(1.2)

        return lessons

    def synthesize_master_playbook(self, videos: List[Dict[str, Any]], lessons: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Synthesizes all chronological lessons into PrimeFX Master Playbook (Logic vs Flaws)."""
        tutorials = [l for l in lessons if l.get("video_type") == "TUTORIAL"]
        recaps = [l for l in lessons if l.get("video_type") == "RECAP"]
        sample_highlights = tutorials[:15] + recaps[:10] + lessons[-15:]

        prompt = f"""
Synthesize PrimeFX's complete chronological YouTube library ({len(videos)} videos from oldest {videos[0].get('upload_date') if videos else ''} to latest {videos[-1].get('upload_date') if videos else ''})
into an actionable institutional algorithmic trading playbook for Deriv VIX & Jump Indices.

Extracted Chronological Video Lessons Sample:
{json.dumps(sample_highlights, default=str)[:28000]}

Produce strict PrimeFxMasterPlaybookSchema separating winning edge mechanics from documented risk/discipline flaws we must neutralize with our $100 Institutional Risk Shield.
"""
        parsed = self._call_gemini_structured(prompt, PrimeFxMasterPlaybookSchema)
        if not parsed:
            parsed = {
                "mentor_name": "PrimeFX (@Prime_fx)",
                "framework_summary": "Top-down ICT/SMC pure price-action model trading liquidity raids into high-timeframe POIs across Deriv Volatility & Jump indices.",
                "core_htf_bias_rules": [
                    "Establish directional narrative on Daily / H4 via external vs internal liquidity draw and candle range theory (CRT).",
                    "Require strict candle-body close beyond swing highs/lows for MSS/BOS confirmation (wicks count as liquidity sweeps)."
                ],
                "liquidity_sweep_and_inducement_rules": [
                    "Map Equal Highs (EQH), Equal Lows (EQL), PDH/PDL, and Previous 4H High/Low (P4HH/P4HL).",
                    "Mandatory Inducement (IDM) Rule: Never buy/sell the first minor pullback in front of a POI; wait for IDM to be swept before executing inside the true extreme OB/FVG."
                ],
                "poi_hierarchy_and_refinement": [
                    "H4 macro POI -> H1 structural POI -> M15 execution refinement within 0.618-0.786 OTE / 50% equilibrium.",
                    "Prioritize unmitigated Order Blocks leaving 3-candle FVGs, Inversion FVGs (IFVG), and swept Breaker Blocks."
                ],
                "deriv_synthetic_jump_vix_adaptation": [
                    "No volume-profile, orderflow, or DOM exists on Deriv CSPRNG instruments; rely strictly on OHLC price action and ATR volatility scaling.",
                    "For Jump Indices (JD10-JD100), place SL safely beyond the H1/H4 sweep extreme (>= 1.5*ATR_14) to survive scheduled ~20-minute 30x jump spikes."
                ],
                "winning_trade_checklist": [
                    "1. Clear D1/H4 bias & external liquidity draw",
                    "2. Internal inducement (IDM) / EQH / EQL swept cleanly with wick rejection",
                    "3. Structural displacement (M15/H1 MSS body close leaving FVG)",
                    "4. Entry refined at unmitigated OB/FVG/Breaker with >= 1:2.5 R:R to opposing liquidity"
                ],
                "critical_flaws_to_counter_with_risk_shield": [
                    "FLAW 1 (Account Flipping / Over-Sizing): Titles like 'Full margin on $20' and '2 Accounts Blown' show extreme lot risk causing ruin streaks -> COUNTER: Hard 1.0%-1.5% risk cap ($1.50 max on $100) + mandatory nano-lot viability filter.",
                    "FLAW 2 (Pre-Inducement / Premature Entry): Entering first POI touch before IDM is raided -> COUNTER: Enforce IDM trap tagging & 2-candle confirmation.",
                    "FLAW 3 (Revenge Re-Entry): Re-entering right after SL stop-out -> COUNTER: Enforce mandatory 2-hour cooldown after any loss.",
                    "FLAW 4 (Failing to Secure Profits): Allowing +2R trades to reverse to loss -> COUNTER: Move SL to Break-Even at +1.0R and sweep 50% profit into Vault at +20% milestones."
                ],
                "_ai_model": "synthesized_fallback"
            }

        parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
        parsed["total_videos_analyzed"] = len(videos)
        parsed["chronological_span"] = {
            "oldest": f"#{videos[0]['chronological_rank']} ({videos[0]['upload_date']}) - {videos[0]['title']}" if videos else "",
            "latest": f"#{videos[-1]['chronological_rank']} ({videos[-1]['upload_date']}) - {videos[-1]['title']}" if videos else "",
        }
        return parsed

    def export_outputs(self, videos: List[Dict[str, Any]], lessons: List[Dict[str, Any]], playbook: Dict[str, Any]):
        for p in [self.catalog_path, self.curriculum_report_path, self.master_playbook_json, self.logic_and_flaws_md]:
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)

        lesson_by_id = {l.get("video_id"): l for l in lessons}
        catalog_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "channel_source": videos[0]["channel"] if videos else "Prime_Fx",
            "total_videos": len(videos),
            "sort_order": "chronological_oldest_to_latest",
            "videos": [
                {
                    **v,
                    "lesson": lesson_by_id.get(v["id"], {})
                }
                for v in videos
            ]
        }
        with open(self.catalog_path, "w") as f:
            json.dump(catalog_payload, f, indent=2)

        with open(self.master_playbook_json, "w") as f:
            json.dump(playbook, f, indent=2)

        # Export Syllabus report
        curr_lines = [
            "# 🎓 PRIMEFX CHRONOLOGICAL VIDEO CURRICULUM (`OLDEST ➔ LATEST`)",
            f"*Channel Source: `{catalog_payload['channel_source']}` | Total Videos Ingested: **`{len(videos)}`** | Updated: `{catalog_payload['generated_at'][:19]} UTC`*",
            "*Trained sequentially from foundational videos (#1) to recent executions to learn winning Liquidity/POI setups while isolating risk & discipline flaws.*\n",
            "| # | Upload Date | Type | Instruments Covered | Video Title & Link |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        for v in videos:
            inst_str = ", ".join(v.get("instruments", [])[:3])
            clean_title = v["title"].replace("|", "—")
            curr_lines.append(
                f"| **#{v['chronological_rank']}** | {v.get('upload_date', 'N/A')} | `{v['video_type']}` | {inst_str} | [{clean_title}]({v['url']}) |"
            )
        with open(self.curriculum_report_path, "w") as f:
            f.write("\n".join(curr_lines) + "\n")

        # Export Logic & Flaws Playbook Markdown
        md_lines = [
            "# 📘 PRIMEFX SYNTHESIZED PLAYBOOK: WINNING EDGE VS. CRITICAL RISK FLAWS",
            f"*Derived from **{len(videos)}** videos chronologically (`{playbook['chronological_span'].get('oldest', '')[:45]}` ➔ `{playbook['chronological_span'].get('latest', '')[:45]}`)*\n",
            "## 1. Framework Core & Pure Price-Action Philosophy",
            f"{playbook.get('framework_summary', '')}\n",
            "### High-Timeframe Bias & Range Rules",
            *[f"- {r}" for r in playbook.get("core_htf_bias_rules", [])],
            "\n### Liquidity Sweep & Inducement (IDM) Mechanics",
            *[f"- {r}" for r in playbook.get("liquidity_sweep_and_inducement_rules", [])],
            "\n### POI Hierarchy & Timeframe Refinement",
            *[f"- {r}" for r in playbook.get("poi_hierarchy_and_refinement", [])],
            "\n### Deriv Volatility & Jump Index Adaptation (Zero Orderflow / Zero Volume Profile)",
            *[f"- {r}" for r in playbook.get("deriv_synthetic_jump_vix_adaptation", [])],
            "\n## 2. Winning Trade Blueprints (`What Wins`)",
            *[f"- ✅ {r}" for r in playbook.get("winning_trade_checklist", [])],
            "\n## 3. Critical Flaws & Failure Modes Counter-Balanced by Our Risk Shield (`Where He Fails`)",
            *[f"- 🛡️ {r}" for r in playbook.get("critical_flaws_to_counter_with_risk_shield", [])],
        ]
        with open(self.logic_and_flaws_md, "w") as f:
            f.write("\n".join(md_lines) + "\n")

    def run(self, per_video: bool = False) -> Dict[str, Any]:
        videos = self.discover_videos()
        transcripts = self.fetch_all_transcripts(videos)
        lessons = self.extract_curriculum_chronologically(videos, transcripts, per_video=per_video)
        playbook = self.synthesize_master_playbook(videos, lessons)
        self.export_outputs(videos, lessons, playbook)
        logger.info(f"PrimeFX Training Pipeline Complete -> {self.catalog_path}, {self.curriculum_report_path}, {self.master_playbook_json}")
        return {
            "total_videos": len(videos),
            "lessons_extracted": len(lessons),
            "playbook": playbook,
        }


if __name__ == "__main__":
    trainer = PrimeFxTrainer()
    trainer.run()
