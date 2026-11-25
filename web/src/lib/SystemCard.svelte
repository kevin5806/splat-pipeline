<script>
	import { api } from '#lib/api';
	import { onMount } from 'svelte';

	let sys = $state(null);
	let error = $state('');

	async function load() {
		try {
			sys = await api('/system');
			error = '';
		} catch (e) {
			error = e.message;
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 3000);
		return () => clearInterval(t);
	});
	const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);
</script>

<div class="card grid">
	<div class="row">
		<h3 class="grow">Sistema</h3>
		{#if sys?.fake}<span class="chip warn">modalità simulata</span>{/if}
		{#if sys?.running}<span class="chip run">in esecuzione: {sys.running}</span>{:else if sys}<span class="chip">libero</span>{/if}
	</div>
	{#if error}
		<p class="mut small" style="margin: 0">Backend non raggiungibile: {error}</p>
	{:else if sys}
		<div class="grid three">
			<div>
				<div class="small mut">GPU</div>
				{#if sys.gpus.length}
					{#each sys.gpus as g}
						<div style="font-weight: 600">{g.name}</div>
						<div class="small mut">Uso {g.util}% · {g.temp_c} °C</div>
						<div class="bar" style="margin: 4px 0"><i style="width: {g.util}%"></i></div>
						<div class="small mut">VRAM {g.mem_used_mb} / {g.mem_total_mb} MB</div>
						<div class="bar" style="margin-top: 4px"><i style="width: {pct(g.mem_used_mb, g.mem_total_mb)}%"></i></div>
					{/each}
				{:else}
					<div class="small mut">nessuna GPU NVIDIA visibile</div>
				{/if}
			</div>
			<div>
				<div class="small mut">CPU</div>
				<div style="font-weight: 600">{sys.cpu_count} core</div>
				<div class="small mut">Carico {sys.load1 ?? '–'}</div>
			</div>
			<div>
				<div class="small mut">Disco libero</div>
				<div style="font-weight: 600">{sys.disk_free_gb} GB</div>
				<div class="bar" style="margin-top: 4px">
					<i style="width: {100 - pct(sys.disk_free_gb, sys.disk_total_gb)}%"></i>
				</div>
				<div class="small mut">su {sys.disk_total_gb} GB</div>
			</div>
		</div>
	{/if}
</div>
