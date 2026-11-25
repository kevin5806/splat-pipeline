<script>
	import '../app.css';
	import { getToken, setToken } from '#lib/api';
	import { onMount } from 'svelte';

	let { children } = $props();
	let needToken = $state(false);
	let token = $state('');

	// Se il backend risponde 401 (PANEL_TOKEN impostato), chiede il token una volta.
	onMount(async () => {
		const res = await fetch('/api/projects', { headers: getToken() ? { 'X-Token': getToken() } : {} });
		needToken = res.status === 401;
	});

	function save() {
		setToken(token.trim());
		location.reload();
	}
</script>

{#if needToken}
	<div class="wrap" style="max-width: 420px; padding-top: 80px">
		<div class="card grid">
			<h2>Accesso al pannello</h2>
			<p class="mut small" style="margin: 0">Questo pannello è protetto: inserisci il token impostato in <span class="mono">PANEL_TOKEN</span>.</p>
			<input type="password" bind:value={token} placeholder="Token" onkeydown={(e) => e.key === 'Enter' && save()} />
			<button class="primary" onclick={save}>Entra</button>
		</div>
	</div>
{:else}
	{@render children()}
{/if}
