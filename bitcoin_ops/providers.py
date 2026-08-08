"""Blockchain data and broadcast provider access."""

from typing import Any

import requests
from embit.transaction import Transaction
from requests.exceptions import RequestException


class BlockchainProvider:
    """Access blockchain data through Bitcoin Core or a mempool-compatible API."""

    def __init__(
        self,
        network_name: str = "test",
        rpc_url: str | None = None,
        use_rpc: bool = False,
        rpc_only: bool = False,
        http: Any | None = None,
        api_base: str | None = None,
    ):
        self.network_name = network_name
        self.use_rpc = use_rpc
        self.rpc_url = rpc_url
        self.rpc_only = rpc_only
        self.http = http or requests.Session()

        default_api_base = (
            "https://mempool.space/testnet/api"
            if network_name == "test"
            else "https://mempool.space/api"
        )
        self.api_base = (api_base or default_api_base).rstrip("/")

    def _rpc_call(self, method: str, params: list, timeout: int = 10) -> Any:
        """Make an RPC call to Bitcoin Core"""
        if not self.rpc_url:
            return None

        try:
            payload = {
                "jsonrpc": "1.0",
                "id": "bitcoin-ops",
                "method": method,
                "params": params,
            }
            response = self.http.post(self.rpc_url, json=payload, timeout=timeout)

            if response.status_code == 200:
                result = response.json()
                if result.get("error"):
                    # Don't print error here, let caller handle it
                    return None
                return result.get("result")
            return None
        except RequestException:
            return None

    def _print_txindex_warning(self) -> None:
        """Print warning message about txindex not being enabled"""
        print("\n✗ ERROR: txindex is not enabled on your Bitcoin Core node")
        print("\n  This tool requires txindex=1 when using RPC mode.")
        print("\n  To enable txindex:")
        print("    1. Add 'txindex=1' to your bitcoin.conf")
        print("    2. Restart Bitcoin Core (it will reindex the blockchain)")
        print("    3. Wait for reindexing to complete (may take several hours)")
        print("\n  Or run without RPC flags to use mempool.space API only")
        print(
            "\n  Alternatively, use --rpc-only for slow scantxoutset mode (no txindex needed)"
        )

    def check_txindex_enabled(self) -> bool:
        """Check if txindex is enabled on the Bitcoin Core node"""
        if not self.rpc_url:
            return False

        # Try to get blockchain info which includes txindex status
        result = self._rpc_call("getblockchaininfo", [])
        if result and "blocks" in result:
            # Node is accessible, check if we can fetch a non-coinbase transaction
            # Use a known testnet transaction (not genesis/coinbase which can't be fetched)
            # This is one of the user's successful OP_RETURN transactions from history
            test_txid = (
                "63611617ee33c761c2c9586d0f998baa16bfd876b703921a6a1b31c2933abf64"
            )
            test_result = self._rpc_call("getrawtransaction", [test_txid, False])

            return bool(test_result)
        return False

    def fetch_utxos(self, address: str) -> list[dict]:
        """Fetch all UTXOs for an address from RPC or mempool.space API"""
        if self.use_rpc and self.rpc_url:
            return self._fetch_utxos_rpc(address)
        else:
            return self._fetch_utxos_api(address)

    def _fetch_utxos_rpc(self, address: str) -> list[dict]:
        """Fetch UTXOs using Bitcoin Core RPC"""
        try:
            if self.rpc_only:
                # Use scantxoutset to find UTXOs (RPC only mode)
                print("  Using Bitcoin Core RPC only (scantxoutset)...")
                print(
                    "  ⚠️  Warning: This may take 30-60 seconds to scan the UTXO set..."
                )

                # Use scantxoutset to find UTXOs for this address
                result = self._rpc_call(
                    "scantxoutset",
                    ["start", [f"addr({address})"]],
                    timeout=120,  # Increase timeout for scantxoutset
                )

                if not result:
                    print("  ✗ scantxoutset failed or returned no results")
                    return []

                # Convert scantxoutset format to our format
                utxos = []
                for unspent in result.get("unspents", []):
                    utxos.append(
                        {
                            "txid": unspent["txid"],
                            "vout": unspent["vout"],
                            "value": int(
                                unspent["amount"] * 100_000_000
                            ),  # BTC to sats
                            "status": {
                                "confirmed": True
                            },  # scantxoutset only returns confirmed
                        }
                    )

                print(f"  ✓ Found {len(utxos)} UTXOs via scantxoutset")
                return utxos
            else:
                # Hybrid mode: use mempool.space for discovery, RPC for verification
                print(
                    "  Using Bitcoin Core RPC (hybrid mode with mempool.space for discovery)..."
                )

                # Note: We use mempool.space to find UTXOs since scantxoutset is slow
                # and listunspent requires the address to be imported into the wallet.
                # For transaction fetching and broadcasting, we'll still use RPC.
                print("  → Getting UTXO list from mempool.space (faster)...")
                utxos = self._fetch_utxos_api(address)

                # If we found UTXOs via API, verify they exist via RPC
                if utxos and self.rpc_url:
                    print("  → Verifying UTXOs with local node...")
                    verified_utxos = []
                    for utxo in utxos:
                        # Try to get the transaction via RPC to verify it exists locally
                        tx_result = self._rpc_call(
                            "getrawtransaction", [utxo["txid"], True]
                        )
                        if tx_result:
                            # UTXO exists in local node
                            verified_utxos.append(utxo)

                    if verified_utxos:
                        print(
                            f"  ✓ Verified {len(verified_utxos)}/{len(utxos)} UTXOs exist in local node"
                        )
                        return verified_utxos
                    else:
                        print(
                            "  ⚠️  Warning: UTXOs not found in local node (may not be fully synced)"
                        )
                        print("  Using mempool.space data anyway...")

                return utxos

        except Exception as e:
            print(f"  ✗ RPC error: {e}")
            if not self.rpc_only:
                print("  Falling back to mempool.space API only...")
                return self._fetch_utxos_api(address)
            return []

    def _fetch_utxos_api(self, address: str) -> list[dict]:
        """Fetch all UTXOs for an address from mempool.space API"""
        try:
            print("  Using mempool.space API...")
            url = f"{self.api_base}/address/{address}/utxo"
            response = self.http.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            print(f"✗ Error fetching UTXOs: {e}")
            return []

    def fetch_transaction(self, txid: str) -> Transaction | None:
        """Fetch transaction by txid from RPC or API"""
        if self.use_rpc and self.rpc_url:
            return self._fetch_transaction_rpc(txid)
        else:
            return self._fetch_transaction_api(txid)

    def _fetch_transaction_rpc(self, txid: str) -> Transaction | None:
        """Fetch transaction using Bitcoin Core RPC (requires txindex=1)"""
        try:
            result = self._rpc_call("getrawtransaction", [txid, False])
            if result:
                return Transaction.parse(bytes.fromhex(result))

            # If RPC failed, txindex is likely not enabled
            print(
                f"\n✗ ERROR: Could not fetch transaction {txid[:16]}... from local node"
            )
            print(
                "  This usually means txindex is not enabled in your Bitcoin Core node."
            )
            print("\n  To enable txindex:")
            print("    1. Add 'txindex=1' to your bitcoin.conf")
            print("    2. Restart Bitcoin Core (it will reindex the blockchain)")
            print("    3. Wait for reindexing to complete")
            print("\n  Or run without RPC flags to use mempool.space API only")
            return None
        except Exception as e:
            print(f"\n✗ RPC transaction fetch failed: {e}")
            print("  Make sure txindex=1 is enabled in bitcoin.conf")
            return None

    def _fetch_transaction_api(self, txid: str) -> Transaction | None:
        """Fetch transaction by txid from mempool.space API"""
        try:
            url = f"{self.api_base}/tx/{txid}/hex"
            response = self.http.get(url, timeout=10)
            response.raise_for_status()
            # Parse hex transaction string into Transaction object
            return Transaction.parse(bytes.fromhex(response.text.strip()))
        except RequestException as e:
            print(f"✗ Error fetching transaction: {e}")
            return None
        except Exception as e:
            print(f"✗ Error parsing transaction: {e}")
            return None

    def display_utxos(self, utxos: list[dict]) -> None:
        """Display available UTXOs in a formatted way"""
        if not utxos:
            print("No UTXOs found for this address.")
            return

        print(f"\nAvailable UTXOs ({len(utxos)} found):")
        print("-" * 80)
        for i, utxo in enumerate(utxos):
            btc_value = utxo["value"] / 100_000_000
            print(f"[{i}] TXID: {utxo['txid']}")
            print(f"    VOUT: {utxo['vout']}")
            print(f"    Amount: {utxo['value']} sats ({btc_value:.8f} BTC)")
            print(
                f"    Status: {'Confirmed' if utxo.get('status', {}).get('confirmed') else 'Unconfirmed'}"
            )
            print()


# Backward-compatible name for callers using the original single-file API.
UTXOManager = BlockchainProvider
