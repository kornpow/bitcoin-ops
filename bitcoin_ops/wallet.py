"""Wallet key management."""

import os

from embit import ec, script
from embit.networks import NETWORKS

from .errors import BitcoinOpsError


class WalletManager:
    """Manages wallet key generation, loading, and persistence"""

    def __init__(self, wallet_file: str = "wallet.key", network_name: str = "test"):
        self.wallet_file = wallet_file
        self.network = NETWORKS[network_name]
        self.priv_key: ec.PrivateKey | None = None
        self.pub_key: ec.PublicKey | None = None
        self.address: str | None = None

    def load_or_generate_key(self) -> tuple[ec.PrivateKey, ec.PublicKey, str]:
        """Load existing key or generate new one and save to filesystem"""
        if os.path.exists(self.wallet_file):
            print(f"✓ Loading existing wallet from {self.wallet_file}")
            self.priv_key = self._load_key()
        else:
            print(f"✓ Generating new wallet and saving to {self.wallet_file}")
            self.priv_key = self._generate_and_save_key()

        self.pub_key = self.priv_key.get_public_key()
        script_pubkey = script.p2wpkh(self.pub_key)
        address = script_pubkey.address(network=self.network)
        if address is None:
            raise BitcoinOpsError("Failed to derive address from public key")
        self.address = address

        return self.priv_key, self.pub_key, self.address

    def _load_key(self) -> ec.PrivateKey:
        """Load private key from wallet file"""
        try:
            with open(self.wallet_file) as f:
                wif = f.read().strip()

            if not wif:
                raise ValueError("Wallet file is empty")

            priv_key = ec.PrivateKey.from_wif(wif)
            return priv_key
        except (OSError, ValueError) as exc:
            raise BitcoinOpsError(f"Error loading wallet: {exc}") from exc

    def _generate_and_save_key(self) -> ec.PrivateKey:
        """Generate new private key and save to filesystem"""
        try:
            # Generate random 256-bit private key
            privdata = os.urandom(32)
            priv_key = ec.PrivateKey(privdata)

            # Convert to WIF format for storage
            wif = priv_key.wif(network=self.network)

            # Save to file with restricted permissions
            with open(self.wallet_file, "w") as f:
                f.write(wif)

            # Set file permissions to read/write for owner only (Unix-like systems)
            if hasattr(os, "chmod"):
                os.chmod(self.wallet_file, 0o600)

            print(f"✓ Private key saved to {self.wallet_file}")
            print("⚠️  IMPORTANT: Keep this file secure! It contains your private key.")

            return priv_key
        except (OSError, ValueError) as exc:
            raise BitcoinOpsError(f"Error generating/saving wallet: {exc}") from exc
