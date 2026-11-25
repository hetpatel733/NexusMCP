import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'

const rootDir = path.resolve(__dirname, '..')
const envDir = fs.existsSync(path.resolve(rootDir, '.env')) ? rootDir : __dirname

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
    envDir,
    server: {
        port: 5173,
        host: true
    }
})
