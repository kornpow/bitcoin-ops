# Security Policy

Bitcoin operations software handles private keys and can create irreversible
transactions. Treat this project as experimental software and review every
transaction before broadcasting it.

## Supported Versions

Security fixes are applied to the latest commit on the default branch. Older
commits and forks are not supported.

## Reporting a Vulnerability

Do not disclose suspected vulnerabilities in a public issue, discussion, or
pull request.

Use GitHub's private vulnerability reporting feature for this repository when
it is available. Otherwise, contact the repository maintainer through a private
channel and include:

- the affected commit or version;
- reproduction steps or a proof of concept;
- the potential impact and affected networks;
- any suggested mitigation; and
- whether the report may be credited publicly.

Avoid including real private keys, wallet files, RPC passwords, seed phrases,
or signed mainnet transactions. Use regtest or testnet data for reproductions.
The maintainer should acknowledge a report within seven days and coordinate a
fix and disclosure timeline based on severity.

## Operational Security

- Use testnet or regtest until the full workflow has been independently tested.
- Keep wallet files outside shared or synced directories and retain mode `0600`.
- Never pass RPC passwords in shell history when a cookie-authenticated local
  node or another protected credential mechanism is available.
- Verify the network, inputs, outputs, fee, change address, and transaction hex
  with a second tool before broadcasting.
- Use a dedicated wallet with limited funds; do not use this tool as a vault.
- Back up keys securely before funding an address. Losing the wallet file makes
  its funds permanently inaccessible.
- Assume OP_RETURN and witness data are public and permanent once confirmed.

## Scope

Reports about key generation and storage, transaction construction or signing,
fee and change calculation, network separation, RPC credential exposure, and
unsafe broadcast behavior are in scope. Availability problems in third-party
APIs and upstream Bitcoin policy disagreements are generally out of scope
unless this project handles them in a way that creates a security impact.
