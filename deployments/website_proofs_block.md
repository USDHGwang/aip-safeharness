# AIP Protocol — On-Chain Proof Block

> Copy-paste this block into your hackathon submission page.

---

## Live Deployment — 0G Aristotle Mainnet (Chain ID 16661)

| Contract | Address | Explorer |
|---|---|---|
| AIPSensoryLayer v2 | `0xe2073A9bFe630d87C2256357a09AfA918feD41C9` | [View](https://chainscan.0g.ai/address/0xe2073a9bfe630d87c2256357a09afa918fed41c9) |
| AgentExecutor | `0x5715001b3add9724DF93D57E145c2a4330422D0F` | [View](https://chainscan.0g.ai/address/0x5715001b3add9724df93d57e145c2a4330422d0f) |

Deployed by: `0xF343588E6162c5E034feA05d96Dac65123b300c0`

---

## Live Demo Run — Human-in-the-Loop DeFi Intent on Mainnet

**Intent**: `Help me swap 0.1 ETH to USDC at the best available rate, with 1% slippage protection`

### AIP On-Chain Transaction

| Field | Value |
|---|---|
| **Tx Hash** | [`0x5c5d50ca2c6cdaff14c6edf4b433cfbf4fa74a0f179cac78615fee7d152c2f3f`](https://chainscan.0g.ai/tx/0x5c5d50ca2c6cdaff14c6edf4b433cfbf4fa74a0f179cac78615fee7d152c2f3f) |
| **Block** | 32,727,412 |
| **Status** | Success |
| **Caller** | AgentExecutor `0x5715...2D0F` |
| **Events** | IntentOpened + ExecutionTriggered + IntentClosed (3 events, 1 atomic tx) |

### 0G Decentralized Storage Snapshots

Three cryptographically-committed state snapshots uploaded to 0G Storage at each milestone of the intent lifecycle:

| Milestone | When | Root Hash | Link |
|---|---|---|---|
| `awaiting_human` | Before HITL review | `0xbb1a91b973fc637e85e32eb0cae3507636581ab8f73617414fc0c6eac4286dcd` | [Verify](https://indexer-storage-turbo.0g.ai/file?root=0xbb1a91b973fc637e85e32eb0cae3507636581ab8f73617414fc0c6eac4286dcd) |
| `submitting_onchain` | After human approved, before tx | `0x6c64aa7cafbe8cec25e8ac15493555e8dda5ab62ed695147f62f68d0e6cee7c9` | [Verify](https://indexer-storage-turbo.0g.ai/file?root=0x6c64aa7cafbe8cec25e8ac15493555e8dda5ab62ed695147f62f68d0e6cee7c9) |
| `completed` | After tx confirmed | `0x8b95598fde92c8eeba6055d35b8b631187bb5d141f8e984a9132c5b6ab614675` | [Verify](https://indexer-storage-turbo.0g.ai/file?root=0x8b95598fde92c8eeba6055d35b8b631187bb5d141f8e984a9132c5b6ab614675) |

Every hash is a Merkle root of the state snapshot at that point in time. Root hashes are immutable once submitted to 0G Storage.

### Why This Matters

The standard AI agent stack has no execution-layer guarantee: the agent says one thing, but anything can happen on-chain. AIP closes this gap with two checkpoints inside a **single atomic transaction** (EIP-1153 transient storage):

1. **preCheck** — locks the declared intent hash into transient storage
2. **postCheck** — verifies the same hash, confirms no deviation occurred

If the execution deviates from the declared intent at any point, the entire transaction reverts. No partial execution, no residual allowance, no manipulation window.

---

*All proofs are real mainnet transactions. Independently verifiable.*

