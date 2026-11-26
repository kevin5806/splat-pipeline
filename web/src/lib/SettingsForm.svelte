<script>
	import CameraHelp from '#lib/CameraHelp.svelte';
	let { value = $bindable(), disabled = false } = $props();
</script>

<div class="grid two">
	<label class="f">
		Frame totali desiderati (facoltativo)
		<input type="number" min="30" max="6000" step="50" placeholder="es. 800" bind:value={value.target_frames} {disabled} />
		<span class="small">
			Se lo imposti, "un frame ogni N" si calcola da solo e i frame si ripartiscono tra i video in proporzione alla durata
			(un video 4K di 7 minuti con 800 frame è circa 1,9 frame al secondo). Più frame = più copertura, ma COLMAP e il training
			durano di più.
		</span>
	</label>
	<label class="f">
		Un frame ogni N del video {value.target_frames ? '(non usato: hai indicato il totale)' : ''}
		<input type="number" min="1" max="300" bind:value={value.frame_step} disabled={disabled || !!value.target_frames} />
		<span class="small">30 fps e N=16 → circa 1,9 frame/s. Più basso = più frame. <strong>N=1 usa tutti i frame del video: sconsigliato</strong> (migliaia di frame, ore di calcolo e RAM esaurita). Si può anche fissare per singolo video.</span>
	</label>
	<label class="f">
		Fattore di riduzione per il training
		<input type="number" min="1" max="8" bind:value={value.downscale} {disabled} />
		<span class="small">2 = metà risoluzione (4K → 1080p). 1 = piena, serve molta VRAM.</span>
	</label>
	<label class="f">
		Matcher COLMAP
		<select bind:value={value.matcher} {disabled}>
			<option value="vocab_tree">vocab_tree (video con giri chiusi)</option>
			<option value="sequential">sequential (video lineari, più veloce)</option>
			<option value="exhaustive">exhaustive (pochi frame, lentissimo)</option>
		</select>
	</label>
	<label class="f">
		Modello della camera
		<select bind:value={value.camera_model} {disabled}>
			<option value="OPENCV">OPENCV (telefono, obiettivo normale)</option>
			<option value="OPENCV_FISHEYE">OPENCV_FISHEYE (GoPro grandangolo)</option>
			<option value="SIMPLE_RADIAL">SIMPLE_RADIAL (distorsione semplice)</option>
		</select>
	</label>
	<label class="f">
		Recupero dei tratti deboli
		<select bind:value={value.recovery} {disabled}>
			<option value="ask">chiedi conferma (consigliato)</option>
			<option value="auto">automatico</option>
			<option value="off">non fare nulla</option>
		</select>
		<span class="small">
			Dove COLMAP perde la camera, estrae frame più fitti <strong>solo in quei tratti</strong> (il doppio, il quadruplo, fino a tutti i frame del video) e li
			aggancia al modello già costruito. Dopo ogni giro ricontrolla; se infittire non basta, ti chiede un video nuovo di quei punti.
		</span>
	</label>
	<label class="f">
		Passi di training
		<input type="number" min="1000" max="100000" step="1000" bind:value={value.steps} {disabled} />
	</label>
	<label class="f">
		Soglia di qualità (PSNR minimo del test)
		<input type="number" min="0" max="50" step="0.5" bind:value={value.gate_psnr} {disabled} />
		<span class="small">Sotto questa soglia il training finale NON parte e ti viene chiesto cosa fare.</span>
	</label>
	<label class="f">
		Passi del test rapido
		<input type="number" min="500" max="20000" step="500" bind:value={value.test_steps} {disabled} />
	</label>
	<label class="f">
		Ritaglio dello splat (× raggio tipico)
		<input type="number" min="0.5" max="50" step="0.5" bind:value={value.crop_radius} {disabled} />
		<span class="small">Scarta le gaussiane isolate oltre questa distanza. Più alto = tiene più contorno.</span>
	</label>
	<label class="f">
		Cache delle immagini
		<select bind:value={value.cache_images} {disabled}>
			<option value="cpu">cpu (consigliato: serve RAM)</option>
			<option value="gpu">gpu (più veloce, serve molta VRAM)</option>
		</select>
	</label>
	<div style="grid-column: 1 / -1"><CameraHelp /></div>
	<label class="f" style="flex-direction: row; align-items: center; gap: 8px">
		<input type="checkbox" bind:checked={value.skip_test} {disabled} />
		Salta il test di qualità (sconsigliato)
	</label>
</div>
