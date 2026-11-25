<script>
	let { value = $bindable(), disabled = false } = $props();
</script>

<div class="grid two">
	<label class="f">
		Un frame ogni N del video
		<input type="number" min="1" max="300" bind:value={value.frame_step} {disabled} />
		<span class="small">30 fps e N=16 → circa 1,9 frame/s. Più basso = più frame (e più tempo).</span>
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
	<label class="f" style="flex-direction: row; align-items: center; gap: 8px">
		<input type="checkbox" bind:checked={value.skip_test} {disabled} />
		Salta il test di qualità (sconsigliato)
	</label>
</div>
