# Lezioni apprese

Raccolta degli errori e delle scoperte della pipeline originale (video di una camera, 4K, ~7 minuti, Tesla P4).
Sono la ragione di molte scelte del progetto: se vuoi cambiarle, rileggi prima questo.

## 1. Il numero di camere agganciate non misura la qualità

GLOMAP (global mapper) ha agganciato **1764 camere su 1842 (95,8%)**, contro le 1421 del mapper originale (77%).
Sulla carta era molto meglio. In pratica il test di training ha dato **PSNR 14,1 dB** contro 21,2: modello inservibile.

- Le camere "in più" avevano pose quasi inventate: in mediana vedevano **15 punti 3D**, contro 338 delle camere solide.
  L'errore di riproiezione medio le nascondeva (1,12 px contro 1,19).
- Filtrare le camere con meno di 100 punti ha funzionato su un dataset (PSNR 20,95, meglio del mapper originale) e
  **non** sull'altro (16,0). Non esiste una regola sicura: serve un test.
- **Conseguenza di progetto**: il controllo di qualità (test breve + soglia di PSNR) è obbligatorio prima del training
  lungo. È costato ~10 minuti e ha evitato ~1 h 45 di training sbagliato, più di una volta.
- Il mapper incrementale di COLMAP resta il predefinito: più lento (ore con migliaia di camere) ma affidabile.

## 2. Non fidarti dei contatori nei log

Il log del mapper (`Registering image #N (k)`) contava 1666 registrazioni; il modello finale conteneva **1421**
immagini (le altre erano rimosse o ri-registrate). Il pannello legge il numero vero da `images.bin`.

## 3. GPU Pascal (Tesla P4, CUDA capability 6.1)

Tre problemi indipendenti, tutti risolti da `docker/pascal_patch.sh` + `PASCAL_PATCH=1`:

1. `torch.compile` fallisce: *"Found Tesla P4 which is too old to be supported by the triton GPU compiler"*.
   → `TORCHDYNAMO_DISABLE=1`.
2. `CUDA error: no kernel image is available`: il `csrc.so` di gsplat nell'immagine non include sm_61, e l'immagine
   non ha `nvcc` per ricompilarlo. → installare `cuda-nvcc-11-8` e le librerie di sviluppo.
3. La ricompilazione fallisce: `cooperative_groups::labeled_partition` esiste solo da sm_70. 8 punti nel sorgente.
   → sostituzione con un gruppo vuoto (ogni thread fa il proprio `atomicAdd`: stesso risultato, backward appena più lento).

Dettagli utili: sulla P4 il training occupa solo ~1,5 GB di VRAM su 7,6 (il resto che vedi è di altri processi) e usa la
GPU a scatti: il collo di bottiglia è la CPU/Python, non la scheda.

## 4. Gaussiane lontane: lo splat sembra un puntino

Lo splat esportato aveva l'1,5% delle gaussiane a 20-25 unità dal centro, mentre la stanza (99% dei punti) stava in
±1,3. I viewer inquadrano tutto il contenuto, quindi la scena appariva minuscola. `app/tools/crop.py` scarta ciò che sta
oltre 3,5 × il 90° percentile della distanza dal centro e ricentra. Non modifica le gaussiane rimaste (le armoniche
sferiche, cioè i colori che dipendono dall'angolo, restano intatte).

## 5. L'asse "su" di uno splat non è affidabile

L'intestazione del `.ply` di Nerfstudio dice `Vertical Axis: z`, ma è un testo fisso: nella pratica abbiamo avuto
viewer con la stanza ruotata o con il mouse che "piegava il collo" invece di ruotare. Le pose delle camere e l'impressione
visiva non coincidevano. Soluzione adottata nel viewer di prova: un selettore "Alto = asse" (±X, ±Y, ±Z) che ricalcola i
controlli senza ricaricare il file. Idea ancora da fare: stimare la verticale in automatico (piani di pavimento/soffitto).

## 6. Nomi di file che oscurano librerie

Uno script chiamato `cv2.py` nella cartella da cui partiva Python ha rotto `import cv2` dentro Nerfstudio
(*"partially initialized module ... circular import"*). Nel progetto i moduli si chiamano `convert.py`, `crop.py`, ecc.,
e vengono lanciati come `python -m app.tools.convert` da `backend/`.

## 7. Docker dentro un LXC (Proxmox)

- Aumentare i core del container LXC si applica a caldo al cgroup, ma il **demone Docker ha letto "4 CPU" all'avvio** e
  rifiuta `--cpus` più alti. Si aggira scrivendo `cpu.max` nel cgroup del container, oppure riavviando il demone.
- Il pannello dovrebbe girare con `restart: unless-stopped`; i processi lanciati a mano con `nohup` **non sopravvivono** a un
  riavvio del container.

## 8. Tempi misurati (video 4K, 7 min, Tesla P4, 828-1842 frame)

| Fase | Tempo |
|---|---|
| Estrazione di 828 frame | 2 min |
| Feature COLMAP | ~2 min |
| Matching `vocab_tree`, 828 frame | ~25 min; 1842 frame: ~2,5 h (con pochi core) |
| Mapper incrementale, 828 frame | ~18 min; 1842 frame: ~2 h |
| Bundle adjustment, 1666 camere | **65 min** (molto più del previsto) |
| Test di qualità (4000 passi) | ~10 min |
| Training da 30000 passi | 106-112 min |
| Export + ritaglio | ~3 min |

## 9. Come è stata sprecata una notte

Una pipeline di riserva (mapper originale) era pronta ma **non collegata** a quella che falliva (GLOMAP): quando il controllo di
qualità ha fermato la prima, nessuno script aspettava il mapper, e il server è rimasto fermo ~12 ore.
Regola: ogni catena automatica deve avere il suo fallback già collegato, oppure fermarsi in modo visibile e
chiedere una decisione (il pannello mostra la fase "bloccata" con il motivo).

## 10. Il mapper che riparte scrive in un altro posto

Una ricostruzione nuova finisce in `sparse/<n>/`; ripartendo da uno snapshot (`--input_path`) il mapper scrive in
`sparse/`. Il codice guardava solo le sottocartelle e, dopo un aggiornamento fatto durante il mapping, ha preso un
frammento da 2 camere al posto del modello da 1.541. Ora si cercano entrambe le posizioni, si sceglie per numero di
camere e un controllo ferma il lavoro se ne restano pochissime. «La ripresa parte» non vuol dire «la ripresa funziona»:
va verificata fino al risultato.

## 11. Più video: Nerfstudio ignora le sottocartelle

Con le immagini ridotte Nerfstudio cerca `images_<n>/<nome del file>`: `v1/frame_00001.jpg` e `v2/frame_00001.jpg` si
sovrappongono. Il dataset usa nomi unici (`v1__frame_00001.jpg`) e la conversione controlla che esistano tutti i file
che verranno aperti.

## 12. `--load-dir` aggiunge passi

`ns-train --load-dir ... --max-num-iterations N` fa N passi **in più** di quelli già fatti: ripreso a 600 chiedendone
900, si è fermato a 1.500. Alla ripresa si chiedono solo i passi mancanti. Per lo stesso motivo «Rifai da qui» sul
training sposta via il vecchio checkpoint (lo conserva), altrimenti risulterebbe già completo.

## 13. Dove mancano le camere, infittire funziona (spesso)

Due video da circa 4 minuti, un frame ogni 8: 1.541 camere su 1.735. Estraendo un frame ogni 4 **solo** nei tratti
deboli (501 immagini in più, circa 45 minuti) se ne sono agganciate 441, più 132 foto di prima rimaste fuori: il video
poco coperto è passato da 62 a 14,5 secondi. Non serve rifare la ricostruzione: feature e matching delle sole immagini
nuove, poi il mapper continua il modello con le pose vecchie ferme. La copertura va misurata come distanza tra camere
buone consecutive: contando i frame mancati, più si infittisce più il risultato sembra peggiore.

Con altri due giri (un frame ogni 2, poi tutti) restano 2,2 secondi scoperti e 2.694 camere. **Non rinunciare a un
tratto perché un giro non l'ha migliorato**: uno è passato da 6,7 a 6,5 secondi al passo 4 e si è sistemato al passo 1.


- Build completa dell'immagine Docker (con e senza `PASCAL_PATCH`) e prova end-to-end su un video reale.
- Stima automatica dell'asse "su" e correzione dell'orientamento nell'export.
- Fasi COLMAP: i flag sono quelli di `ns-process-data`, ma qui vengono lanciati direttamente; il matching `sequential` non è
  stato provato su un video reale.
- Il modello `OPENCV_FISHEYE` con splatfacto non è stato provato.
