import 'dotenv/config';
import { Indexer, ZgFile } from '@0gfoundation/0g-ts-sdk';
import { ethers } from 'ethers';

const NETWORKS = {
    testnet: {
        evmRpc: 'https://evmrpc-testnet.0g.ai',
        indRpc: 'https://indexer-storage-testnet-turbo.0g.ai',
    },
    mainnet: {
        evmRpc: 'https://evmrpc.0g.ai',
        indRpc: 'https://indexer-storage-turbo.0g.ai',
    },
};
const network = (process.env.NETWORK || 'testnet').toLowerCase();
const { evmRpc, indRpc } = NETWORKS[network] ?? NETWORKS.testnet;

const privateKey = network === 'mainnet'
    ? process.env.ZEROG_MAINNET_PRIVATE_KEY
    : process.env.ZEROG_PRIVATE_KEY;

if (!privateKey) {
    const keyName = network === 'mainnet' ? 'ZEROG_MAINNET_PRIVATE_KEY' : 'ZEROG_PRIVATE_KEY';
    console.error(JSON.stringify({error: `Set ${keyName} in .env`}));
    process.exit(1);
}

const filePath = process.argv[2];
if (!filePath) {
    console.error(JSON.stringify({error: 'file path required'}));
    process.exit(1);
}

async function upload() {
    try {
        const provider = new ethers.JsonRpcProvider(evmRpc);
        const signer = new ethers.Wallet(privateKey, provider);
        const indexer = new Indexer(indRpc);
        
        const zgFile = await ZgFile.fromFilePath(filePath);
        const [tx, err] = await indexer.upload(zgFile, evmRpc, signer);
        
        if (err) {
            console.error(JSON.stringify({error: String(err)}));
            process.exit(2);
        }
        
        console.log(JSON.stringify({
            root_hash: tx.rootHash,
            tx_hash: tx.txHash,
            tx_seq: tx.txSeq
        }));
        
        await zgFile.close();
    } catch (e) {
        console.error(JSON.stringify({error: String(e)}));
        process.exit(3);
    }
}

upload();
