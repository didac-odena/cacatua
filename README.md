# Whisper dictation

Dictado por voz local para Windows usando `faster-whisper`. Equivalente al dictado nativo (Win+H) pero con mejor transcripción, idioma español, arranque automático y watchdog.

## Qué hace

- Pulsas **F9** desde cualquier ventana → empieza a grabar (beep agudo).
- Hablas. Puedes pausar para pensar; no se corta solo.
- Pulsas **F9** otra vez → para, transcribe y **pega el texto donde tenías el cursor** (aunque mientras tanto hayas cambiado de ventana).
- **Ctrl+Alt+Q** → salir del todo.
- Se arranca solo al iniciar sesión y si el proceso muere (o lo matas desde el Administrador de tareas) se relanza solo en 3 segundos.

## Requisitos

- Windows 10 u 11.
- Python 3.11 o superior ([python.org](https://www.python.org/downloads/)). Al instalar, marca "Add Python to PATH".
- GPU NVIDIA (opcional pero recomendado). Sin NVIDIA funciona igual pero en CPU, mucho más lento.
- ~3 GB de disco para el entorno virtual + modelo.

## Instalación rápida

```powershell
git clone <url-del-repo> C:\dev\whisper
cd C:\dev\whisper
powershell -ExecutionPolicy Bypass -File install.ps1
```

El instalador hace todo: crea el venv, instala dependencias, detecta si hay GPU NVIDIA y añade las librerías CUDA si corresponde, y crea el acceso directo de inicio automático.

Para arrancar sin reiniciar:

```powershell
wscript.exe launch.vbs
```

La primera vez tarda ~30s extra porque descarga el modelo Whisper (`small` por defecto, ~500 MB).

## Uso

| Atajo | Acción |
|---|---|
| F9 | Empezar / parar grabación |
| Ctrl+Alt+Q | Salir |

La ventanita flotante es ahora una píldora de estado minimalista: solo aparece durante la grabación/transcripción/carga de modelo y se esconde sola. Puedes arrastrarla con el ratón a donde te venga mejor (por ejemplo si te tapa algo al hacer un pantallazo); la posición se guarda en `config.json`.

Junto al reloj de Windows aparece un icono en la bandeja del sistema con el estado del programa:
- **gris** = esperando.
- **rojo parpadeando** = escuchando.
- **naranja** = transcribiendo (o cargando modelo).

Haciendo clic derecho en el icono tienes un menú con:
- **Micrófono**: elige el dispositivo de entrada (se guarda en `config.json`). En "Editar lista…" puedes ocultar los dispositivos que no uses nunca (desmárcalos); vuelven a aparecer si los marcas de nuevo.
- **Modelo**: cambia entre `small` (rápido) y `medium` (más preciso). Mientras carga, la píldora muestra "Cargando…".
- **Sonidos**: activa/desactiva los beeps de inicio/fin de grabación.
- **Reiniciar**: equivalente a `restart.bat` pero sin salir del icono de bandeja — mata y relanza el proceso.
- **Salir**: cierre limpio (equivalente a Ctrl+Alt+Q).

## Reiniciar si se buguea

Doble clic en `restart.bat` (o `restart` desde cmd en la carpeta). Mata los procesos y relanza el watchdog.

## Desinstalar

1. Borra `Whisper.lnk` de la carpeta de inicio:
   - Pulsa **Win+R**, escribe `shell:startup`, Enter. Borra `Whisper.lnk`.
2. Mata los procesos (`restart.bat` o Administrador de tareas, mata `pythonw.exe` y `wscript.exe`).
3. Borra la carpeta `C:\dev\whisper`.

## Archivos

| Archivo | Qué es |
|---|---|
| `main.py` | App principal: hotkey, UI flotante, grabación, transcripción, pegado. |
| `launch.vbs` | Lanzador silencioso con watchdog (relanza si crashea). |
| `install.ps1` | Instalador one-shot. |
| `restart.bat` | Reinicia la app. |
| `requirements.txt` | Dependencias Python. |
| `config.json` | Se genera solo. Guarda micro, modelo, sonidos on/off, micros ocultos de la lista y posición de la píldora flotante. |

## Configuración avanzada

Todo se cambia en constantes al principio de `main.py`:

- `HOTKEY` / `EXIT_HOTKEY`: atajos de teclado.
- `LANGUAGE`: idioma (`"es"` por defecto).
- `INITIAL_PROMPT`: frase modelo para guiar el estilo de puntuación.
- `VAD_PARAMETERS`: sensibilidad al silencio. Si te corta ráfagas, sube `min_silence_duration_ms`.
- `DEFAULT_MODEL`: `small` (rápido) o `medium` (más preciso).

## Problemas comunes

- **`cublas64_12.dll not found`**: el `install.ps1` no pudo copiar las DLLs de CUDA. Vuelve a ejecutarlo.
- **`float16 not supported`**: GPU antigua (pre-Turing). El código ya lo maneja cayendo a `int8_float32`.
- **No transcribe nada / audio silencioso**: revisa el micrófono seleccionado en el menú del icono de bandeja (clic derecho → Micrófono).
- **Pega en la ventana equivocada**: la aplicación destino debe aceptar `Ctrl+V`. Terminales antiguas o apps raras pueden no responder.

## Licencia

Uso personal.
