import { sveltekit } from '@sveltejs/kit/vite';
import adapter from '@sveltejs/adapter-static';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			// Pannello a pagina singola: si compila in file statici serviti dal backend.
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	server: {
		// in sviluppo le chiamate /api vanno al backend (SPLAT_FAKE=1 per provarlo senza GPU)
		proxy: { '/api': 'http://localhost:8080' }
	}
});
