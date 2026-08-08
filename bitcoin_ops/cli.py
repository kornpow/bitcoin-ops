"""Command-line interface for bitcoin-ops."""

import argparse
import os

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException

from .errors import BitcoinOpsError
from .providers import BlockchainProvider
from .transactions import OPReturnTransactionBuilder
from .wallet import WalletManager


def positive_fee_rate(value: str) -> float:
    """Parse and validate a positive fee rate."""
    try:
        fee_rate = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("fee rate must be a number") from exc
    if fee_rate <= 0:
        raise argparse.ArgumentTypeError("fee rate must be greater than zero")
    return fee_rate


def select_utxo(utxos: list[dict], index: int | None = None) -> dict:
    """Select an explicit UTXO or default to the largest available one."""
    if not utxos:
        raise ValueError("no UTXOs available")
    if index is not None:
        if index < 0 or index >= len(utxos):
            raise IndexError(f"UTXO index must be between 0 and {len(utxos) - 1}")
        return utxos[index]
    return max(utxos, key=lambda utxo: utxo["value"])


def confirm_mainnet_broadcast(network: str, assume_yes: bool = False) -> bool:
    """Require explicit approval before sending a mainnet transaction."""
    if network != "main" or assume_yes:
        return True
    print("\n⚠️  WARNING: This will broadcast to MAINNET (real Bitcoin)!")
    try:
        return input("Type 'yes' to confirm: ").strip().lower() == "yes"
    except EOFError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create Bitcoin transactions on testnet: OP_RETURN or witness data storage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check wallet balance
  bitcoin-ops -b

  # Store data via OP_RETURN (immediately readable, ≤80 bytes)
  bitcoin-ops --op-return "Hello Bitcoin!" -x

  # Store a file via OP_RETURN
  bitcoin-ops --op-return --file poem.txt -x

  # Store large data (up to 10KB) via P2WSH witness script
  bitcoin-ops --witness "my data here" -x
  bitcoin-ops --witness --file data.bin -x

  # Reveal witness data on-chain by spending the P2WSH output
  bitcoin-ops --spend TXID:VOUT --witness --file data.bin -x

  # Custom fee rate
  bitcoin-ops --op-return "hi" --fee-rate 5 -x

  # Use environment variable for wallet location
  export BITCOIN_OPS_WALLET=~/wallets/testnet.key
  bitcoin-ops -b

Environment Variables:
  BITCOIN_OPS_WALLET    Path to wallet file (overrides --wallet-file)
  BITCOIN_OPS_API_URL   Base URL for a mempool-compatible API
        """,
    )

    parser.add_argument(
        "--wallet-file",
        default="wallet.key",
        help="Path to wallet key file (default: wallet.key, supports ~ expansion)",
    )
    parser.add_argument(
        "--network",
        default="test",
        choices=["test", "main"],
        help="Bitcoin network (default: test)",
    )

    # --- Data mode flags ---
    parser.add_argument(
        "--op-return",
        dest="op_return",
        type=str,
        nargs="?",  # 0 or 1 value: --op-return "text" OR --op-return (uses --file)
        const="",  # present with no value → use --file
        action="append",
        metavar="TEXT",
        help="Store data via OP_RETURN output (≤80 bytes; use multiple times for multiple outputs). "
        "Alias: --data",
    )
    parser.add_argument(
        "--data",  # legacy alias, same dest
        dest="op_return",
        type=str,
        action="append",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--witness",
        dest="witness_text",
        type=str,
        nargs="?",
        const="",  # present with no value → use --file
        metavar="TEXT",
        help="Store arbitrary data (up to 10KB) via P2WSH witness script. "
        "Pass text inline or use --file. Aliases: --witness-data",
    )
    parser.add_argument(
        "--witness-data",  # legacy alias
        dest="witness_text",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--file",
        dest="file",
        type=str,
        metavar="PATH",
        help="File to read data from. Used with --op-return or --witness.",
    )
    parser.add_argument(
        "--witness-file",  # legacy alias
        dest="file",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--spend",
        dest="spend_p2wsh",
        type=str,
        metavar="TXID:VOUT",
        help="Spend a P2WSH witness-data output, revealing its data on-chain. "
        "Requires --witness or --file to reconstruct the witness script. "
        "Alias: --spend-p2wsh",
    )
    parser.add_argument(
        "--spend-p2wsh",  # legacy alias
        dest="spend_p2wsh",
        type=str,
        help=argparse.SUPPRESS,
    )

    # --- Common flags ---
    parser.add_argument(
        "--fee-rate",
        type=positive_fee_rate,
        default=2.0,
        help="Fee rate in sat/vB (default: 2.0, supports fractional rates like 0.5)",
    )
    parser.add_argument(
        "-b",
        "--balance",
        dest="check_balance",
        action="store_true",
        help="Check wallet balance and available UTXOs. Alias: --check-balance",
    )
    parser.add_argument(
        "--check-balance",  # legacy alias
        dest="check_balance",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show all historical OP_RETURN transactions from this wallet",
    )
    parser.add_argument(
        "--utxo",
        dest="utxo_index",
        type=int,
        metavar="N",
        help="Index of UTXO to use (if multiple available). Alias: --utxo-index",
    )
    parser.add_argument(
        "--utxo-index",  # legacy alias
        dest="utxo_index",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--large",
        dest="allow_large_opreturn",
        action="store_true",
        help="Allow OP_RETURN data >80 bytes (may not relay on standard nodes). "
        "Alias: --allow-large-opreturn",
    )
    parser.add_argument(
        "--allow-large-opreturn",  # legacy alias
        dest="allow_large_opreturn",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-x",
        "--broadcast",
        dest="broadcast",
        action="store_true",
        help="Automatically broadcast transaction to mempool.space",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Confirm mainnet broadcast non-interactively (use with extreme care)",
    )

    parser.add_argument(
        "--api-url",
        help="Base URL for a mempool-compatible API (or BITCOIN_OPS_API_URL)",
    )

    # --- RPC flags (unchanged) ---
    parser.add_argument(
        "--rpc-url",
        type=str,
        help="Bitcoin Core RPC URL (e.g., http://user:pass@localhost:8332)",
    )
    parser.add_argument("--rpc-user", type=str, help="Bitcoin Core RPC username")
    parser.add_argument("--rpc-password", type=str, help="Bitcoin Core RPC password")
    parser.add_argument(
        "--rpc-host",
        type=str,
        default="localhost",
        help="Bitcoin Core RPC host (default: localhost)",
    )
    parser.add_argument(
        "--rpc-port",
        type=int,
        help="Bitcoin Core RPC port (default: 8332 for mainnet, 18332 for testnet)",
    )
    parser.add_argument(
        "--rpc-only",
        action="store_true",
        help="Use ONLY local Bitcoin Core RPC (scantxoutset for UTXO discovery, slower but no external API)",
    )

    return parser


def main(argv: list[str] | None = None):
    args = build_parser().parse_args(argv)
    http = requests.Session()

    # Resolve wallet file path - support environment variable and expand paths
    wallet_file = os.getenv("BITCOIN_OPS_WALLET", args.wallet_file)
    wallet_file = os.path.expanduser(wallet_file)  # Expand ~ to home directory
    wallet_file = os.path.abspath(wallet_file)  # Convert to absolute path

    # Create parent directory if it doesn't exist
    wallet_dir = os.path.dirname(wallet_file)
    if wallet_dir and not os.path.exists(wallet_dir):
        try:
            os.makedirs(wallet_dir, mode=0o700)
            print(f"✓ Created wallet directory: {wallet_dir}")
        except OSError as e:
            print(f"✗ Error creating wallet directory: {e}")
            return

    # Initialize wallet
    print("=" * 80)
    print("Bitcoin OP_RETURN Transaction Creator")
    print("=" * 80)

    wallet_mgr = WalletManager(wallet_file, args.network)
    try:
        _, _, address = wallet_mgr.load_or_generate_key()
    except BitcoinOpsError as exc:
        print(f"✗ {exc}")
        return

    print(f"\n{'Testnet' if args.network == 'test' else 'Mainnet'} Address: {address}")
    print("=" * 80)

    # Build RPC URL if credentials provided
    rpc_url = None
    use_rpc = False
    rpc_only = args.rpc_only
    if args.rpc_url:
        rpc_url = args.rpc_url
        use_rpc = True
    elif args.rpc_user and args.rpc_password:
        # Default ports
        port = args.rpc_port or (18332 if args.network == "test" else 8332)
        rpc_url = f"http://{args.rpc_user}:{args.rpc_password}@{args.rpc_host}:{port}"
        use_rpc = True

    # Initialize UTXO manager
    api_base = os.getenv("BITCOIN_OPS_API_URL", args.api_url)
    utxo_mgr = BlockchainProvider(
        args.network,
        rpc_url=rpc_url,
        use_rpc=use_rpc,
        rpc_only=rpc_only,
        http=http,
        api_base=api_base,
    )

    # If using RPC, check that txindex is enabled
    if use_rpc and not rpc_only:
        print("\n⌛ Checking Bitcoin Core configuration...")
        if not utxo_mgr.check_txindex_enabled():
            utxo_mgr._print_txindex_warning()
            return
        print("  ✓ txindex is enabled")

    # Check if history mode
    if args.history:
        print("\n⌛ Fetching transaction history...")

        try:
            # Fetch all transactions for this address
            api_base = utxo_mgr.api_base

            # Get address transactions
            tx_url = f"{api_base}/address/{address}/txs"
            response = http.get(tx_url, timeout=10)
            response.raise_for_status()
            transactions = response.json()

            # Filter for transactions with OP_RETURN outputs
            op_return_txs = []
            for tx in transactions:
                for vout in tx.get("vout", []):
                    if vout.get("scriptpubkey_type") == "op_return":
                        op_return_txs.append(
                            {
                                "txid": tx["txid"],
                                "status": tx.get("status", {}),
                                "vout": vout,
                                "fee": tx.get("fee", 0),
                                "size": tx.get("size", 0),
                            }
                        )
                        break  # Only count each tx once

            if not op_return_txs:
                print("\n📝 No OP_RETURN transactions found for this address")
                return

            print(f"\n📜 Found {len(op_return_txs)} OP_RETURN transaction(s):")
            print("=" * 80)

            for i, tx in enumerate(op_return_txs):
                confirmed = tx["status"].get("confirmed", False)
                block_height = tx["status"].get("block_height", "N/A")

                print(f"\n[{i + 1}] TXID: {tx['txid']}")
                print(f"    Status: {'✓ Confirmed' if confirmed else '⌛ Unconfirmed'}")
                if confirmed:
                    print(f"    Block: {block_height}")
                print(f"    Fee: {tx['fee']} sats")
                print(f"    Size: {tx['size']} bytes")

                # Decode OP_RETURN data
                script_hex = tx["vout"]["scriptpubkey"]
                try:
                    # Skip OP_RETURN opcode (6a) and get the data
                    # Handle different push opcodes
                    if script_hex[:2] == "6a":
                        script_bytes = bytes.fromhex(script_hex)

                        # Determine data location based on push opcode
                        if len(script_bytes) > 1:
                            second_byte = script_bytes[1]

                            if second_byte <= 75:
                                # Direct push (0x01-0x4b)
                                data_start = 2
                                data_len = second_byte
                            elif second_byte == 0x4C:
                                # OP_PUSHDATA1
                                data_start = 3
                                data_len = script_bytes[2]
                            elif second_byte == 0x4D:
                                # OP_PUSHDATA2
                                data_start = 4
                                data_len = int.from_bytes(script_bytes[2:4], "little")
                            else:
                                # Unknown format
                                data_start = 2
                                data_len = len(script_bytes) - 2

                            data = script_bytes[data_start : data_start + data_len]

                            # Try to decode as UTF-8
                            try:
                                decoded = data.decode("utf-8")
                                print(f'    Data: "{decoded}"')
                            except UnicodeDecodeError:
                                print(f"    Data (hex): {data.hex()}")

                            print(f"    Data length: {len(data)} bytes")
                except Exception as e:
                    print(f"    Data: (could not decode: {e})")

                # Link to view on mempool.space
                if args.network == "test":
                    print(f"    View: https://mempool.space/testnet/tx/{tx['txid']}")
                else:
                    print(f"    View: https://mempool.space/tx/{tx['txid']}")

            print("\n" + "=" * 80)
            print(f"Total OP_RETURN transactions: {len(op_return_txs)}")

        except RequestException as e:
            print(f"\n✗ Error fetching transaction history: {e}")

        return

    # Fetch UTXOs
    print("\n⌛ Fetching UTXOs...")
    utxos = utxo_mgr.fetch_utxos(address)
    utxo_mgr.display_utxos(utxos)

    if not utxos:
        print("\n⚠️  No funds available!")
        print("\n📝 To get testnet coins, visit a faucet:")
        print("   • https://testnet-faucet.mempool.co/")
        print("   • https://coinfaucet.eu/en/btc-testnet/")
        print(f"\n   Send coins to: {address}")
        return

    # If just checking balance, exit here
    if args.check_balance:
        total = sum(u["value"] for u in utxos)
        print(f"\n💰 Total balance: {total} sats ({total / 100_000_000:.8f} BTC)")
        return

    # Handle witness data: --witness "text" or --witness --file path or --witness-data (legacy)
    witness_data = None
    if args.witness_text and args.witness_text != "":
        witness_data = args.witness_text.encode("utf-8")
    elif args.file and not args.op_return:
        # --file without --op-return → witness mode
        try:
            with open(os.path.expanduser(args.file), "rb") as f:
                witness_data = f.read()
        except Exception as e:
            print(f"✗ Error reading file: {e}")
            return
    elif args.witness_text == "" and args.file:
        # --witness --file path
        try:
            with open(os.path.expanduser(args.file), "rb") as f:
                witness_data = f.read()
        except Exception as e:
            print(f"✗ Error reading file: {e}")
            return

    # Validate witness data size
    if witness_data is not None:
        if len(witness_data) > 10240:
            print(
                f"✗ ERROR: Witness data too large ({len(witness_data)} bytes, max 10KB)"
            )
            return
        print(
            f"\n✓ Witness data loaded: {len(witness_data)} bytes (stored in witness, not OP_RETURN)"
        )

    # Resolve --op-return --file path (OP_RETURN from file)
    op_return_list = list(args.op_return) if args.op_return else []
    if args.file and args.op_return is not None:
        # Replace any empty sentinel ("") entries with file content
        file_text = None
        for i, val in enumerate(op_return_list):
            if val == "":
                if file_text is None:
                    try:
                        with open(os.path.expanduser(args.file)) as f:
                            file_text = f.read()
                    except Exception as e:
                        print(f"✗ Error reading file: {e}")
                        return
                op_return_list[i] = file_text

    # Need data to create transaction
    if not op_return_list and not witness_data and not args.spend_p2wsh:
        print(
            "\n⚠️  Use --op-return for OP_RETURN, --witness for witness storage, or -b to view balance"
        )
        return

    # Handle --spend: spend an existing P2WSH witness-data output
    if args.spend_p2wsh:
        if witness_data is None:
            print(
                "✗ ERROR: --spend requires --witness or --file to reconstruct the witness script"
            )
            return

        try:
            parts = args.spend_p2wsh.split(":")
            if len(parts) != 2:
                raise ValueError("expected TXID:VOUT")
            p2wsh_txid = parts[0]
            p2wsh_vout = int(parts[1])
        except ValueError as e:
            print(f"✗ ERROR: Invalid --spend value ({e}). Expected format: TXID:VOUT")
            return

        # Fetch the P2WSH output amount from the API
        print(f"\n⌛ Fetching P2WSH output {p2wsh_txid[:16]}...:{p2wsh_vout}...")
        prev_tx = utxo_mgr.fetch_transaction(p2wsh_txid)
        if not prev_tx:
            print("✗ Failed to fetch P2WSH transaction")
            return
        p2wsh_amount = prev_tx.vout[p2wsh_vout].value
        print(f"  Amount: {p2wsh_amount} sats")

        # If P2WSH output alone can't cover fee + dust change, consolidate with
        # the first available P2WPKH UTXO to ensure a valid change output
        extra_utxo = None
        extra_prev_tx = None
        if p2wsh_amount < 800:  # rough threshold: fee ~270 + dust 546
            if utxos:
                extra_utxo = utxos[0]
                print(
                    f"\n  P2WSH output ({p2wsh_amount} sats) too small for fee+change alone."
                )
                print(
                    f"  Consolidating with UTXO {extra_utxo['txid'][:16]}...:{extra_utxo['vout']} ({extra_utxo['value']} sats)"
                )
                extra_prev_tx = utxo_mgr.fetch_transaction(extra_utxo["txid"])
                if not extra_prev_tx:
                    print("✗ Failed to fetch consolidation UTXO transaction")
                    return
            else:
                print(
                    "⚠️  P2WSH output too small for fee+change and no P2WPKH UTXOs available to consolidate."
                )

        print("\n⌛ Building P2WSH spending transaction...")
        builder = OPReturnTransactionBuilder(wallet_mgr, fee_rate=args.fee_rate)
        final_tx = builder.spend_p2wsh_transaction(
            p2wsh_txid,
            p2wsh_vout,
            p2wsh_amount,
            witness_data,
            extra_utxo=extra_utxo,
            extra_prev_tx=extra_prev_tx,
        )

        print("\n" + "=" * 80)
        print("✓ P2WSH spending transaction created!")
        print("  The full witness script (with embedded data) is in the witness field.")
        print("=" * 80)
        tx_hex = final_tx.to_string()
        print(f"\nTransaction Hex:\n{tx_hex}")
        print("\n" + "=" * 80)

        if args.broadcast:
            if not confirm_mainnet_broadcast(args.network, args.yes):
                print("Broadcast cancelled")
                return
            if args.network == "test":
                broadcast_url = "https://mempool.space/testnet/api/tx"
            else:
                broadcast_url = "https://mempool.space/api/tx"
            print("\n⌛ Broadcasting to mempool.space...")
            try:
                response = http.post(broadcast_url, data=tx_hex, timeout=10)
                if response.status_code == 200:
                    txid = response.text.strip()
                    print("\n✓ Broadcast successful!")
                    print(f"  TXID: {txid}")
                    if args.network == "test":
                        print(f"  View: https://mempool.space/testnet/tx/{txid}")
                    else:
                        print(f"  View: https://mempool.space/tx/{txid}")
                else:
                    print(
                        f"\n✗ Broadcast failed! Status {response.status_code}: {response.text}"
                    )
            except RequestException as e:
                print(f"\n✗ Network error during broadcast: {e}")
        else:
            if args.network == "test":
                print(
                    "\n📡 To broadcast: run with -x, or paste hex at https://mempool.space/testnet/tx/push"
                )
            else:
                print(
                    "\n📡 To broadcast: run with -x, or paste hex at https://mempool.space/tx/push"
                )
        return

    # Select UTXO
    try:
        selected_utxo = select_utxo(utxos, args.utxo_index)
    except IndexError as e:
        print(f"✗ Invalid UTXO index: {e}")
        return

    print(f"\n✓ Using UTXO: {selected_utxo['txid']}:{selected_utxo['vout']}")
    print(f"  Amount: {selected_utxo['value']} sats")

    # Fetch previous transaction
    print("\n⌛ Fetching previous transaction...")
    prev_tx = utxo_mgr.fetch_transaction(selected_utxo["txid"])
    if not prev_tx:
        print("✗ Failed to fetch previous transaction")
        return

    # Build and sign transaction
    print("\n⌛ Building transaction...")
    builder = OPReturnTransactionBuilder(wallet_mgr, fee_rate=args.fee_rate)

    # Convert all data strings to bytes
    op_return_data_list = (
        [d.encode("utf-8") for d in op_return_list] if op_return_list else []
    )

    # Display all OP_RETURN data
    if op_return_data_list:
        print(
            f"✓ Creating transaction with {len(op_return_data_list)} OP_RETURN output(s):"
        )
        total_data_size = 0
        for i, data in enumerate(op_return_data_list):
            print(f'  [{i + 1}] Data: "{op_return_list[i]}"')
            print(f"      Bytes: {data.hex()}")
            print(f"      Length: {len(data)} bytes")
            total_data_size += len(data)

        print(f"\n  Total OP_RETURN data size: {total_data_size} bytes")
    else:
        print("✓ Creating transaction without OP_RETURN data")

    if witness_data:
        print(f"✓ Witness data included: {len(witness_data)} bytes")

    # Warn about multiple OP_RETURN outputs
    if len(op_return_data_list) > 1:
        print(
            f"\n⚠️  WARNING: Transaction has {len(op_return_data_list)} OP_RETURN outputs"
        )
        print(
            "  Bitcoin Core's default policy rejects multiple OP_RETURN outputs (multi-op-return)"
        )
        print("  This is a STANDARDNESS rule, not a consensus rule.")
        print("\n  This transaction will NOT propagate on the standard network!")
        print("\n  To broadcast, you need a custom Bitcoin Core node with:")
        print("    -datacarriersize=<size>  (for total data size)")
        print("    -permitbaremultisig=1    (allows multiple OP_RETURN)")
        print("\n  Note: Most explorers and nodes will reject this transaction.")

    # Check size limits for each OP_RETURN
    max_data_size = 0
    if op_return_data_list:
        max_data_size = max(len(d) for d in op_return_data_list)
        if max_data_size > 80 and not args.allow_large_opreturn:
            print(
                f"\n✗ ERROR: One or more OP_RETURN outputs exceed 80 bytes (largest: {max_data_size} bytes)"
            )
            print(
                "\n  Bitcoin Core's default policy (-datacarriersize=80) rejects OP_RETURN >80 bytes"
            )
            print(
                "  Most nodes and services (including mempool.space) won't relay these transactions"
            )
            print("\n  Solutions:")
            print("    1. Shorten your messages to ≤80 bytes each")
            print("    2. Use --large flag (transaction may not broadcast)")
            print("    3. Broadcast to a node with higher -datacarriersize setting")
            return

        if max_data_size > 80:
            print(
                f"⚠️  WARNING: One or more OP_RETURN outputs exceed 80 bytes (largest: {max_data_size} bytes)"
            )
            print("  This transaction likely won't relay on standard nodes!")
            print(
                f"  You'll need to broadcast to a custom node with -datacarriersize={max_data_size} or higher"
            )

        if max_data_size > 10000:
            print(f"\n✗ ERROR: OP_RETURN data is too large ({max_data_size} bytes)")
            print("  Maximum reasonable size is around 10KB")
            return

    tx = builder.create_transaction(
        selected_utxo["txid"],
        selected_utxo["vout"],
        selected_utxo["value"],
        op_return_data_list,
        prev_tx,
        witness_data=witness_data,
    )

    print("⌛ Signing transaction...")
    final_tx = builder.sign_transaction(
        tx,
        selected_utxo["txid"],
        selected_utxo["vout"],
        prev_tx.vout[selected_utxo["vout"]],
        witness_data=witness_data,
    )

    print("\n" + "=" * 80)
    print("✓ Transaction created successfully!")
    print("=" * 80)

    tx_hex = final_tx.to_string()
    print(f"\nTransaction Hex:\n{tx_hex}")
    print("\n" + "=" * 80)

    # Broadcast if requested
    if args.broadcast or args.rpc_url or args.rpc_user:
        if not confirm_mainnet_broadcast(args.network, args.yes):
            print("Broadcast cancelled")
            return
        # Determine broadcast method
        use_rpc = args.rpc_url or args.rpc_user

        if use_rpc:
            # Broadcast to local Bitcoin Core node via RPC
            print("\n⌛ Broadcasting transaction to Bitcoin Core RPC...")

            # Build RPC URL
            port: int = (
                args.rpc_port
                if args.rpc_port
                else (18332 if args.network == "test" else 8332)
            )
            if args.rpc_url:
                rpc_url = args.rpc_url
            else:
                # Construct from components
                if not args.rpc_user or not args.rpc_password:
                    print("✗ ERROR: RPC user and password required")
                    print(
                        "  Use --rpc-user and --rpc-password, or provide full --rpc-url"
                    )
                    return

                rpc_url = (
                    f"http://{args.rpc_user}:{args.rpc_password}@{args.rpc_host}:{port}"
                )

            try:
                # Make RPC call to sendrawtransaction
                rpc_payload = {
                    "jsonrpc": "1.0",
                    "id": "bitcoin-ops",
                    "method": "sendrawtransaction",
                    "params": [tx_hex],
                }

                response = http.post(rpc_url, json=rpc_payload, timeout=10)

                if response.status_code == 200:
                    result = response.json()

                    if result.get("error"):
                        print(f"\n✗ RPC error: {result['error']}")

                        # Provide helpful error messages
                        error_msg = str(result["error"])
                        if "bad-txns-inputs-missingorspent" in error_msg:
                            print("  This usually means the UTXO was already spent")
                        elif "min relay fee" in error_msg:
                            print("  Transaction fee is too low, increase --fee-rate")
                        elif (
                            "scriptpubkey" in error_msg
                            or "mandatory-script-verify-flag" in error_msg
                        ):
                            print(
                                "  Script validation failed - check OP_RETURN data size"
                            )
                    else:
                        txid = result.get("result", "")
                        print("\n✓ Transaction broadcast successful via RPC!")
                        print(f"  TXID: {txid}")

                        if args.network == "test":
                            print("\n  View on mempool.space:")
                            print(f"  https://mempool.space/testnet/tx/{txid}")
                        else:
                            print("\n  View on mempool.space:")
                            print(f"  https://mempool.space/tx/{txid}")
                else:
                    print("\n✗ RPC request failed!")
                    print(f"  Status code: {response.status_code}")
                    print(f"  Response: {response.text}")

            except RequestsConnectionError:
                print("\n✗ Connection error: Could not connect to Bitcoin Core RPC")
                print(
                    f"  URL: {rpc_url.replace(args.rpc_password if args.rpc_password else '', '****') if args.rpc_password else rpc_url}"
                )
                print("\n  Make sure:")
                print("    1. Bitcoin Core is running")
                print("    2. RPC server is enabled (server=1 in bitcoin.conf)")
                print("    3. Credentials are correct")
                print(f"    4. RPC port is correct ({port})")

            except RequestException as e:
                print(f"\n✗ Network error during RPC broadcast: {e}")

        else:
            # Broadcast to mempool.space
            print("\n⌛ Broadcasting transaction to mempool.space...")

            if args.network == "test":
                broadcast_url = "https://mempool.space/testnet/api/tx"
            else:
                broadcast_url = "https://mempool.space/api/tx"

            try:
                response = http.post(broadcast_url, data=tx_hex, timeout=10)

                if response.status_code == 200:
                    txid = response.text.strip()
                    print("\n✓ Transaction broadcast successful!")
                    print(f"  TXID: {txid}")

                    if args.network == "test":
                        print("\n  View on mempool.space:")
                        print(f"  https://mempool.space/testnet/tx/{txid}")
                    else:
                        print("\n  View on mempool.space:")
                        print(f"  https://mempool.space/tx/{txid}")
                else:
                    print("\n✗ Broadcast failed!")
                    print(f"  Status code: {response.status_code}")
                    print(f"  Response: {response.text}")

                    # Try to parse error message
                    try:
                        error_msg = response.text
                        if "scriptpubkey" in error_msg.lower():
                            print(
                                "\n  This error usually means the OP_RETURN data is too large (>80 bytes)"
                            )
                            print(f"  Largest OP_RETURN is {max_data_size} bytes")
                    except Exception:
                        pass

            except RequestException as e:
                print(f"\n✗ Network error during broadcast: {e}")
    else:
        # Show manual broadcast instructions
        if args.network == "test":
            print("\n📡 Broadcast options:")
            print("   • Run with -x to use mempool.space")
            print("   • Run with --rpc-user/--rpc-password to use local Bitcoin Core")
            print(
                "   • Or manually paste hex at: https://mempool.space/testnet/tx/push"
            )
        else:
            print("\n⚠️  MAINNET TRANSACTION - Verify carefully before broadcasting!")
            print("\n📡 Broadcast options:")
            print("   • Run with -x to use mempool.space")
            print("   • Run with --rpc-user/--rpc-password to use local Bitcoin Core")
            print("   • Or manually paste hex at: https://mempool.space/tx/push")


if __name__ == "__main__":
    main()
