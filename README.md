# splat-pipeline

Da un video a un modello 3D (**Gaussian Splatting**) elaborato **in locale**, con un pannello web per vedere il
progresso. Niente servizi cloud: tutto gira sulla tua macchina con una GPU NVIDIA.

```
video ──► frame ──► COLMAP (pose delle camere) ──► test di qualità ──► training splatfacto ──► .ply
                                                    └─ se il PSNR è basso: si ferma e te lo dice
```

Costruito sopra [Nerfstudio](https://docs.nerf.studio/) (COLMAP, ffmpeg, splatfacto). Questo progetto aggiunge
l'orchestrazione, la ripresa dopo interruzioni, il controllo di qualità e il pannello.

## Avvio rapido con l'immagine pronta

Le immagini si costruiscono con GitHub Actions e stanno su GHCR: non serve compilare nulla.

```bash
# GPU recenti (Volta in su: RTX 20xx, 30xx, 40xx, A100...)
docker run -d --gpus all --shm-size 16g -p 8080:8080 -p 7007:7007 -v "$PWD/data:/data"   --name splat-pipeline ghcr.io/kevin5806/splat-pipeline:latest

# GPU Pascal (Tesla P4/P40, GTX 10xx): variante con la patch a gsplat
docker run -d --gpus all --shm-size 16g -p 8080:8080 -p 7007:7007 -v "$PWD/data:/data"   --name splat-pipeline ghcr.io/kevin5806/splat-pipeline:latest-pascal
```

Con compose: `IMAGE_TAG=latest-pascal docker compose up -d` (oppure senza variabile per la standard).
Poi apri `http://<macchina>:8080`. L'immagine pesa ~17 GB (contiene Nerfstudio, COLMAP e CUDA).

## Avvio rapido, costruendo l'immagine in locale

Servono Docker con il [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/) e una GPU NVIDIA.

```bash
cp .env.example .env          # opzionale: porte, token, ecc.
docker compose up -d --build
```

Apri `http://<server>:8080`, trascina un video e premi **Crea progetto**.

**GPU vecchie (Pascal: Tesla P4/P40, GTX 10xx)**: serve una patch a `gsplat` (la build dura 10-20 minuti in più):

```bash
PASCAL_PATCH=1 docker compose up -d --build
```

Vedi [docs/LESSONS.md](docs/LESSONS.md) per il perché.

## Cosa fa il pannello

- **Progetti**: carichi uno o **più video**, scegli le impostazioni (i predefiniti sono quelli verificati), avvii.
- **Più video nello stesso modello**: ogni video ha un *dispositivo* (telefono, GoPro…) e, se serve, un modello di camera.
  I video dello stesso dispositivo condividono una camera COLMAP, dispositivi diversi ne hanno una ciascuno. Per ogni video
  il pannello mostra quante camere sono state agganciate e **i tratti (in minuti:secondi) dove COLMAP le ha perse**, cioè
  dove conviene riprendere con più sovrapposizione. I video devono avere zone in comune: altrimenti COLMAP non li unisce.
  Aggiungere un video «normale» rifà l'elaborazione da capo; un **video di supporto** (sotto) si aggancia a quella già fatta.
- **Recupero dei tratti deboli**: dopo la ricostruzione trova i punti in cui la camera è persa o agganciata con
  pochissimi punti 3D. Estrae frame più fitti **solo lì** (densità doppia, quadrupla e infine otto volte, cioè tutti i frame con passo 8; ogni volta solo dove il giro
  precedente ha aiutato, e **dopo ogni giro ricontrolla**) e li aggancia al modello esistente, senza rifare matching e ricostruzione e senza muovere le pose già calcolate. Il modello viene
  sostituito solo se non perde camere. Se non basta, mostra le miniature dei punti scoperti e **chiede un video di
  supporto**, che si integra allo stesso modo (anche su progetti già completati; il training si rifà e il modello di prima
  resta da parte). Impostazione `recovery`: `ask` (chiede conferma, predefinito), `auto`, `off`. Aiuta quando mancava
  sovrapposizione; non aiuta con foto mosse o superfici lisce.
- **Fasi con progresso**: analisi, estrazione frame, feature, matching, ricostruzione camere, bundle adjustment, recupero dei tratti deboli,
  preparazione dataset, test di qualità, training, esportazione. Con percentuali, ETA e log per fase.
- **Si riprende da solo**: ogni fase è idempotente e salva stato su disco. Il mapper di COLMAP riparte dagli snapshot
  (ogni 200 immagini), il training dai checkpoint (ogni 1000 passi). Dopo un riavvio del container i progetti
  interrotti ripartono (`AUTO_RESUME=1`).
- **Controllo di qualità**: prima del training lungo fa un test da 4000 passi. Se il PSNR è sotto la soglia
  (18,5 dB di default) **non parte il training finale** e ti chiede cosa fare ("Continua comunque" oppure cambi
  impostazioni e rifai la fase). Ha già evitato ore di training sbagliato.
- **Viewer**: durante il training il viewer live di Nerfstudio (porta 7007); a fine lavoro puoi riavviarlo sul modello.
- **Risultati**: `splat_clean.ply` (ritagliato e ricentrato) e `raw/splat.ply` (originale) scaricabili dal pannello.
- **Una GPU, un progetto alla volta**: gli altri restano in coda.

## Come riprendere le riprese (consigli pratici)

- Esposizione e fuoco **bloccati**; movimenti lenti con spostamenti laterali (serve parallasse, non solo rotazione).
- Più altezze, e parti/finisci da una zona già ripresa per dare a COLMAP un punto di aggancio.
- Nelle pareti bianche inquadra anche spigoli, prese, quadri: danno punti da seguire.
- Con una GoPro: lente Wide (non Lineare), stabilizzazione spenta se possibile e modello camera `OPENCV_FISHEYE`.

## Impostazioni

| Voce | Predefinito | Note |
|---|---|---|
| `frame_step` | 16 | un frame ogni N (30 fps → ~1,9 frame/s) |
| `downscale` | 2 | 4K → 1080p per il training (1 = piena, serve molta VRAM) |
| `matcher` | `vocab_tree` | `sequential` per video lineari, `exhaustive` solo per pochi frame |
| `camera_model` | `OPENCV` | `OPENCV_FISHEYE` per grandangoli |
| `steps` / `test_steps` | 30000 / 4000 | |
| `gate_psnr` | 18.5 | soglia del controllo di qualità |
| `recovery` | `ask` | tratti poco coperti: chiedi conferma, `auto` o `off` |
| `crop_radius` | 3.5 | ritaglio dello splat, in multipli del raggio tipico |

## Variabili d'ambiente

`DATA_DIR`, `PANEL_TOKEN`, `AUTO_RESUME`, `VIEWER_PORT`, `MAX_UPLOAD_GB`, `PASCAL_PATCH`, `SPLAT_FAKE` (vedi sotto).

## Sicurezza

Il pannello può avviare processi pesanti sulla GPU e caricare file grandi. **Non esporlo a internet**: tienilo sulla
rete locale o dietro una VPN/tunnel con autenticazione. Con `PANEL_TOKEN` le API richiedono un token
(header `X-Token`); non sostituisce una vera autenticazione.

## Viewer 3D integrato

Ogni progetto con un `.ply` finito ha il pulsante **Apri nel viewer 3D**: la pagina `/viewer/` del pannello carica lo splat
direttamente dal pannello (nessun servizio esterno). Si cammina con WASD + mouse oppure si orbita; se la stanza risulta
storta si cambia "Alto = asse" (le pose di COLMAP non conoscono la gravità).
Parametri: `?src=<file .ply dello stesso sito>&name=<titolo>&sh=0..3&up=z+`.

Librerie di terzi incluse in `web/static/viewer/lib/` (licenza MIT, con i testi allegati):
[three.js](https://threejs.org/) 0.170.0 e [GaussianSplats3D](https://github.com/mkkellogg/GaussianSplats3D) 0.4.7.

## Sviluppo

```bash
# backend (modalità simulata: nessuna GPU, le fasi fingono di lavorare)
cd backend && pip install -r requirements.txt pytest httpx
SPLAT_FAKE=1 DATA_DIR=./.data STATIC_DIR=../web/build uvicorn app.main:app --port 8080

# pannello (proxy verso il backend)
cd web && npm install && npm run dev

# test
cd backend && pytest
```

Struttura:

```
backend/app/   API (FastAPI), orchestrazione, fasi, strumenti (convert, crop)
backend/tests/ test unitari e dell'intera pipeline in modalità simulata
web/           pannello SvelteKit (si compila in file statici)
docker/        patch Pascal, entrypoint
docs/          lezioni apprese
```

## Stato

Versione 0.1. Le fasi reali sono state ricavate da una pipeline provata su una Tesla P4, ma il pacchetto completo
(immagine Docker + pannello) va ancora verificato end-to-end: vedi la sezione "Da verificare" in
[docs/LESSONS.md](docs/LESSONS.md). Licenza: [MIT](LICENSE).
