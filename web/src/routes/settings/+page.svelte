<script>
	import { api } from '#lib/api';
	import { onMount } from 'svelte';

	let c = $state(null);
	let readOnly = $state(false);
	let message = $state('');
	let error = $state('');
	let busy = $state(false);

	const EVENTS = [
		['blocked', 'serve una mia decisione'],
		['failed', 'un lavoro fallisce'],
		['done', 'un lavoro finisce']
	];

	onMount(async () => {
		try {
			c = await api('/notify');
			if (!c.panel_url) c.panel_url = location.origin;
			readOnly = !!(await api('/system')).read_only;
		} catch (e) {
			error = e.message;
		}
	});

	async function save() {
		error = message = '';
		busy = true;
		try {
			c = await api('/notify', { method: 'PUT', body: c });
			message = 'Salvato.';
			return true;
		} catch (e) {
			error = e.message;
			return false;
		} finally {
			busy = false;
		}
	}
	async function test() {
		if (!(await save())) return;
		busy = true;
		try {
			await api('/notify/test', { method: 'POST' });
			message = 'Salvato e inviata una notifica di prova: controlla che sia arrivata.';
		} catch (e) {
			error = e.message;
		} finally {
			busy = false;
		}
	}
	const toggle = (id, on) => (c.events = on ? [...new Set([...c.events, id])] : c.events.filter((e) => e !== id));
</script>

<div class="wrap grid" style="gap: 16px">
	<div><a href="/">← Tutti i progetti</a></div>
	<div class="card grid">
		<h1>Notifiche</h1>
		{#if c}
			<label class="f">
				Dove mandarle
				<select bind:value={c.kind}>
					<option value="off">spente</option>
					<option value="homeassistant">Home Assistant (webhook)</option>
					<option value="ntfy">ntfy (anche in casa)</option>
					<option value="telegram">Telegram (bot)</option>
					<option value="webhook">webhook generico</option>
				</select>
			</label>
			{#if c.kind === 'homeassistant'}
				<div class="small mut">
					In Home Assistant crea un’automazione con attivatore «Webhook» e, come azione, la notifica che preferisci. Nel messaggio usa
					<code>{'{{ trigger.json.title }}'}</code> e <code>{'{{ trigger.json.message }}'}</code>. Se Home Assistant è in rete locale, il
					messaggio non esce di casa.
				</div>
				<label class="f">Indirizzo del webhook<input bind:value={c.url} placeholder="http://homeassistant.local:8123/api/webhook/…" /></label>
			{:else if c.kind === 'ntfy'}
				<label class="f">Indirizzo con l’argomento<input bind:value={c.url} placeholder="https://ntfy.sh/un-argomento-difficile-da-indovinare" /></label>
				<label class="f">Token (facoltativo)<input bind:value={c.token} autocomplete="off" /></label>
				<div class="small mut">Su ntfy.sh chiunque conosca l’argomento può leggere i messaggi: scegline uno lungo e casuale.</div>
			{:else if c.kind === 'telegram'}
				<label class="f">Token del bot<input bind:value={c.token} autocomplete="off" /></label>
				<label class="f">Chat id<input bind:value={c.chat_id} /></label>
			{:else if c.kind === 'webhook'}
				<label class="f">Indirizzo<input bind:value={c.url} placeholder="https://…" /></label>
				<label class="f">Token «Bearer» (facoltativo)<input bind:value={c.token} autocomplete="off" /></label>
				<div class="small mut">Riceve in JSON: <code>title</code>, <code>message</code>, <code>url</code>.</div>
			{/if}
			{#if c.kind !== 'off'}
				<div class="grid" style="gap: 4px">
					<div class="small">Avvisami quando…</div>
					{#each EVENTS as [id, label]}
						<label class="row small" style="gap: 6px">
							<input type="checkbox" checked={c.events.includes(id)} onchange={(e) => toggle(id, e.target.checked)} />
							{label}
						</label>
					{/each}
				</div>
				<label class="f">Indirizzo del pannello (per il link nella notifica)<input bind:value={c.panel_url} /></label>
				<div class="small mut">
					La notifica contiene il nome del progetto, la fase e il motivo. Con un servizio esterno (ntfy.sh, Telegram) questi dati escono dalla
					tua rete.
				</div>
			{/if}
			<div class="row">
				<button class="primary" disabled={busy || readOnly} onclick={save}>Salva</button>
				{#if c.kind !== 'off'}<button disabled={busy || readOnly} onclick={test}>Salva e invia una prova</button>{/if}
			</div>
		{/if}
		{#if message}<div class="small" style="color: var(--ok)">{message}</div>{/if}
		{#if error}<div class="banner bad small">{error}</div>{/if}
	</div>
</div>
