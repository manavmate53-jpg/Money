"""
Unit tests for Two-Bucket Profit-Lock Vault and Bankroll Auto-Reset Sandbox.
"""

import os
import pytest
from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager


@pytest.fixture
def test_bankroll(tmp_path):
    db_file = os.path.join(tmp_path, "test_trading.db")
    db = DatabaseManager(db_file)
    manager = BankrollManager(db, policy_path="config/bankroll_policy.json")
    return manager, db


def test_initial_bankroll_state(test_bankroll):
    manager, db = test_bankroll
    status = manager.get_status()
    assert status["active_bankroll"] == 100.0
    assert status["vault_balance"] == 0.0
    assert status["epoch_number"] == 1


def test_two_bucket_profit_vault_sweep(test_bankroll):
    manager, db = test_bankroll
    # Baseline: $100.00. Growth trigger is +20% -> $120.00.
    # Simulate trade with +$25.00 profit (bringing bankroll to $125.00).
    # Profit chunk = $25.00.
    # 50% ($12.50) swept into Locked Profit Vault ("Profit Comes Home With Us").
    # 50% ($12.50) compounded into Active Bankroll ($112.50).
    result = manager.process_trade_result(dollar_pnl=25.00, r_multiple=2.5, symbol="1HZ25V")

    assert result["event"] == "PROFIT_VAULT_SWEEP"
    assert result["swept_to_vault"] == 12.50
    assert result["compounded_to_bankroll"] == 12.50
    assert result["new_active_bankroll"] == 112.50
    assert result["total_vaulted"] == 12.50

    status = manager.get_status()
    assert status["active_bankroll"] == 112.50
    assert status["vault_balance"] == 12.50
    assert status["total_accumulated_capital"] == 125.00


def test_simulation_bankruptcy_auto_reset(test_bankroll):
    manager, db = test_bankroll
    # Set starting state with some vaulted profits
    db.set_kv("vault_balance", 20.00)
    db.set_kv("active_bankroll", 25.00)

    # Incur catastrophic loss of -$15.00 -> balance becomes $10.00 <= $15.00 threshold
    res = manager.process_trade_result(dollar_pnl=-15.00, r_multiple=-1.0, symbol="1HZ75V")

    assert res["event"] == "EPOCH_RESET_BANKRUPTCY"
    assert res["old_epoch"] == 1
    assert res["new_epoch"] == 2
    assert res["reset_bankroll"] == 100.00
    assert res["vault_preserved"] == 20.00 # Vaulted profits are strictly preserved!

    status = manager.get_status()
    assert status["epoch_number"] == 2
    assert status["active_bankroll"] == 100.00
    assert status["vault_balance"] == 20.00
