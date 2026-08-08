import pytest
from embit import ec, script
from embit.transaction import Transaction, TransactionOutput

from main import (
    OPReturnTransactionBuilder,
    WalletManager,
    confirm_mainnet_broadcast,
    positive_fee_rate,
    select_utxo,
)


@pytest.fixture
def builder():
    wallet = WalletManager(network_name="test")
    wallet.priv_key = ec.PrivateKey(bytes.fromhex("01" * 32))
    wallet.pub_key = wallet.priv_key.get_public_key()
    return OPReturnTransactionBuilder(wallet)


def test_positive_fee_rate_rejects_zero_and_negative_values():
    assert positive_fee_rate("0.5") == 0.5
    with pytest.raises(Exception, match="greater than zero"):
        positive_fee_rate("0")
    with pytest.raises(Exception, match="greater than zero"):
        positive_fee_rate("-1")


def test_select_utxo_defaults_to_largest_value():
    utxos = [{"value": 1_000}, {"value": 5_000}, {"value": 2_000}]
    assert select_utxo(utxos) == {"value": 5_000}
    assert select_utxo(utxos, 0) == {"value": 1_000}


def test_select_utxo_rejects_negative_index():
    with pytest.raises(IndexError, match="between 0 and 0"):
        select_utxo([{"value": 1_000}], -1)


def test_mainnet_broadcast_requires_exact_confirmation(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert not confirm_mainnet_broadcast("main")
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert confirm_mainnet_broadcast("main")
    assert confirm_mainnet_broadcast("test")
    assert confirm_mainnet_broadcast("main", assume_yes=True)


@pytest.mark.parametrize(
    ("size", "prefix"),
    [(0, "6a00"), (75, "6a4b"), (76, "6a4c4c"), (256, "6a4d0001")],
)
def test_op_return_uses_minimal_push_encoding(builder, size, prefix):
    assert builder._create_op_return_script(b"a" * size).data.hex().startswith(prefix)


def test_create_transaction_validates_previous_output(builder):
    previous = Transaction(
        vout=[TransactionOutput(value=10_000, script_pubkey=script.Script())]
    )
    with pytest.raises(ValueError, match="does not match"):
        builder.create_transaction("00" * 32, 0, 9_999, [], previous)
    with pytest.raises(IndexError, match="outside"):
        builder.create_transaction("00" * 32, 1, 10_000, [], previous)
