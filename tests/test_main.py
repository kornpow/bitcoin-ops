import pytest
from embit import ec, script
from embit.transaction import Transaction, TransactionOutput

from bitcoin_ops.providers import BlockchainProvider
from main import (
    BitcoinOpsError,
    OPReturnTransactionBuilder,
    UTXOManager,
    WalletManager,
    build_parser,
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


def test_parser_can_be_used_without_process_arguments():
    args = build_parser().parse_args(
        ["--network", "main", "--fee-rate", "3.5", "--op-return", "hello"]
    )
    assert args.network == "main"
    assert args.fee_rate == 3.5
    assert args.op_return == ["hello"]


def test_wallet_load_failure_raises_operational_error(tmp_path):
    wallet_file = tmp_path / "invalid.key"
    wallet_file.write_text("not-a-private-key")
    with pytest.raises(BitcoinOpsError, match="Error loading wallet"):
        WalletManager(str(wallet_file)).load_or_generate_key()


def test_utxo_manager_uses_injected_http_session():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"txid": "00" * 32, "vout": 0, "value": 1_000}]

    class Session:
        def __init__(self):
            self.requested_url = ""

        def get(self, url, timeout):
            self.requested_url = url
            assert timeout == 10
            return Response()

    http = Session()
    manager = UTXOManager(http=http)
    assert manager.fetch_utxos("tb1qexample")[0]["value"] == 1_000
    assert http.requested_url.endswith("/address/tb1qexample/utxo")


def test_provider_accepts_configurable_api_endpoint():
    provider = BlockchainProvider(api_base="https://example.test/custom/")
    assert provider.api_base == "https://example.test/custom"


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
