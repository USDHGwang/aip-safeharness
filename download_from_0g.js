import 'dotenv/config';
import { Indexer } from '@0gfoundation/0g-ts-sdk';

const NETWORKS = {
    testnet: { indRpc: 'https://indexer-storage-testnet-turbo.0g.ai' },
    mainnet: { indRpc: 'https://indexer-storage-turbo.0g.ai' },
};
const net = (process.env.NETWORK || 'testnet').toLowerCase();
const { indRpc } = NETWORKS[net] ?? NETWORKS.testnet;

const rootHash = process.argv[2];
const destPath = process.argv[3];

if (!rootHash || !destPath) {
    console.error(JSON.stringify({error: 'usage: download_from_0g.js <root_hash> <dest_path>'}));
    process.exit(1);
}

async function download() {
    try {
        const indexer = new Indexer(indRpc);
        const err = await indexer.download(rootHash, destPath, true);
        if (err) {
            console.error(JSON.stringify({error: String(err)}));
            process.exit(2);
        }
        console.log(JSON.stringify({ok: true, path: destPath}));
    } catch (e) {
        console.error(JSON.stringify({error: String(e)}));
        process.exit(3);
    }
}

download();
