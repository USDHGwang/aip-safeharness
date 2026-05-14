# AIP SafeHarness

**Verifiable AI agent execution lifecycle on 0G Storage + AIP on-chain enforcement**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## What This Is

AIP SafeHarness is the **off-chain Python harness** for the AIP Protocol. It orchestrates a multi-agent loop where:

1. **Agent A** (Gemini) produces a DeFi intent plan
2. **Agent B** (Gemini) reviews and approves/rejects
3. **HITL checkpoint** pauses for human approval
4. **0G Storage** snapshots the agent state at each lifecycle milestone (immutable, verifiable)
5. **AIP on-chain enforcement** submits the intent through `AgentExecutor → AIPSensoryLayer`, ensuring preCheck and postCheck integrity within a single atomic transaction

```text
User intent
    ↓
Agent A plan → Agent B review (PASS) → Human approve (HITL)
    ↓                                       ↓
0G Storage snapshot #1              0G Storage snapshot #2
    ↓
AgentExecutor.execute()
    ↓ preCheck: hash locked in TSTORE
    ↓ action:   intent executed
    ↓ postCheck: hash verified
    ↓
0G Storage snapshot #3
```

Contracts source available verified on [chainscan.0g.ai](https://chainscan.0g.ai/address/0xe2073a9bfe630d87c2256357a09afa918fed41c9#code)

---

## Architecture

| Layer | Component | Location |
|---|---|---|
| Off-chain orchestration | `multi_agent_loop.py` | This repo |
| LLM agents | Gemini API (`gemma-4-26b-a4b-it`) | Google Cloud |
| Decentralized storage | 0G Storage (mainnet) | `upload_to_0g.js` / `zerog_bridge.py` |
| On-chain enforcement | AIPSensoryLayer v2 + AgentExecutor | chainscan.0g.ai (see below) |
| Network | 0G Aristotle Mainnet (Chain ID 16661) | `https://evmrpc.0g.ai` |

---

## Live Mainnet Proof

### Deployed Contracts (0G Aristotle Mainnet, Chain ID 16661)

| Contract | Address | Explorer |
|---|---|---|
| AgentExecutor | `0x5715001b3add9724DF93D57E145c2a4330422D0F` | [View](https://chainscan.0g.ai/address/0x5715001b3add9724df93d57e145c2a4330422d0f) |
| AIPSensoryLayer v2 | `0xe2073A9bFe630d87C2256357a09AfA918feD41C9` | [View](https://chainscan.0g.ai/address/0xe2073a9bfe630d87c2256357a09afa918fed41c9) |

Deployed by: `0xF343588E6162c5E034feA05d96Dac65123b300c0`

### Demo Run #2 (2026-05-09)

**Intent**: `Help me swap 0.1 ETH to USDC at the best available rate, with 1% slippage protection`

**AIP on-chain transaction**

| Field | Value |
|---|---|
| Tx Hash | [`0x5c5d50ca...2f3f`](https://chainscan.0g.ai/tx/0x5c5d50ca2c6cdaff14c6edf4b433cfbf4fa74a0f179cac78615fee7d152c2f3f) |
| Block | 32,727,412 |
| Events | IntentOpened → ExecutionTriggered → IntentClosed (3 events, 1 atomic tx) |

**0G Storage snapshots** (download directly — independently verifiable)

| Milestone | When | Root Hash | Download |
|---|---|---|---|
| `awaiting_human` | Before HITL review | `0xbb1a...6dcd` | [Fetch](https://indexer-storage-turbo.0g.ai/file?root=0xbb1a91b973fc637e85e32eb0cae3507636581ab8f73617414fc0c6eac4286dcd) |
| `submitting_onchain` | After human approved, before tx | `0x6c64...c7c9` | [Fetch](https://indexer-storage-turbo.0g.ai/file?root=0x6c64aa7cafbe8cec25e8ac15493555e8dda5ab62ed695147f62f68d0e6cee7c9) |
| `completed` | After tx confirmed | `0x8b95...4675` | [Fetch](https://indexer-storage-turbo.0g.ai/file?root=0x8b95598fde92c8eeba6055d35b8b631187bb5d141f8e984a9132c5b6ab614675) |

Each root hash is the Merkle root of the full agent state at that moment. Root hashes are immutable once committed to 0G Storage.

---

## Quickstart

### Prerequisites

- Python 3.11+
- Node.js 18+
- Funded 0G Aristotle Mainnet wallet (real OG required — no faucet; buy on KuCoin / Gate.io / MEXC / LBank and withdraw to Chain ID 16661)

### Install

```bash
pip install google-genai web3 eth-abi python-dotenv ddgs
npm install
```

### Environment

Create `.env` (see `.env.example`):

```
NETWORK=mainnet
GEMINI_API_KEY=<your-key>
ZEROG_MAINNET_PRIVATE_KEY=<0x-private-key-of-funded-wallet>
AIP_CONTRACT_MAINNET=0xe2073A9bFe630d87C2256357a09AfA918feD41C9
AGENT_EXECUTOR_MAINNET=0x5715001b3add9724DF93D57E145c2a4330422D0F
```

### Run

```bash
python multi_agent_loop.py
# Enter intent at prompt, then press [a] to approve at HITL checkpoint
```

---

## Limitations

- **Action is mock**: `AgentExecutor.execute()` emits events (`IntentOpened`, `ExecutionTriggered`, `IntentClosed`) but does not perform a real token swap. Swap execution is out of scope for this demo.
- **HITL is audit-tier**: The human-in-the-loop checkpoint records approval in 0G Storage but is not enforced on-chain. A malicious operator could skip it.
- **run_id collisions**: `hitl_review_1` is hardcoded as the HITL run_id. Re-running without resetting `state.json` will trigger the "already completed" resume path. Reset state files between runs.
- **0G Storage verification**: `storagescan.0g.ai` does not reliably index all files. Use `GET https://indexer-storage-turbo.0g.ai/file?root=<hash>` to fetch and verify file content directly.
- **Gemini API rate limits**: Transient 429/500 errors on the Gemini API are expected. The harness retries up to 3 times.

---

## Acknowledgments

- [0G Foundation](https://0g.ai) — decentralized AI operating system, storage network, and Aristotle Mainnet
- [HackQuest](https://hackquest.io) — hackathon platform

---

MIT © 2026 AIP Protocol
