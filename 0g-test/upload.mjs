import 'dotenv/config';
import { Indexer, ZgFile } from '@0gfoundation/0g-ts-sdk';
import { ethers } from 'ethers';
import fs from 'fs';

async function main() {
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
        throw new Error(`Set ${keyName} in .env`);
    }


    console.log("初始化 Provider 與 Signer...");
    const provider = new ethers.JsonRpcProvider(evmRpc);
    const signer = new ethers.Wallet(privateKey, provider);
    const indexer = new Indexer(indRpc);


    console.log("讀取檔案並產生 Merkle Tree...");
    const zgFile = await ZgFile.fromFilePath('test.txt');
    const [tree, treeErr] = await zgFile.merkleTree();

    if (treeErr) {
        throw new Error(`Merkle tree error: ${treeErr}`);
    }
    console.log("File Root Hash:", tree.rootHash());

    console.log("開始上傳至 0G Storage...");
    const [tx, uploadErr] = await indexer.upload(zgFile, evmRpc, signer);

    if (uploadErr) {
        throw new Error(`Upload error: ${uploadErr}`);
    }

    console.log("✅ 上傳成功！Transaction ID:", tx);
    await zgFile.close();
}

main().catch(console.error);
