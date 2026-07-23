# Documentacion de Yarbis

Yarbis es un agente personal local: corre en tu maquina, mantiene memoria
persistente y actua mediante herramientas seguras. Funciona en Windows, Linux,
macOS y Android (Termux), y puede formar una red de nodos propios.

Esta carpeta contiene la documentacion completa. El [README principal](../README.md)
es la portada con instalacion rapida.

## Manual de uso

- **[Manual de uso](manual.md)** — guia paso a paso, orientada a tareas:
  instalar, primer ciclo, canales (Telegram, voz, TUI, PWA movil, Atajos de
  iOS), servicio de fondo, memoria, red de nodos y solucion de problemas.

## Documentacion tecnica

- **[Arquitectura](arquitectura.md)** — como funciona Yarbis por dentro: el
  ciclo del agente, el modelo de estado, la capa por-SO, la malla de nodos y el
  pipeline de memoria.
- **[Referencia de configuracion](configuracion.md)** — cada bloque de
  `state.json` y cada variable de entorno, con defaults.
- **[Referencia de modulos](modulos.md)** — los ~45 modulos Python y su rol.
- **[Catalogo de herramientas](herramientas.md)** — las ~146 tools del agente
  agrupadas por area.

## Cifras de referencia

| Concepto | Valor |
|---|---|
| Modulos Python | ~45 |
| Herramientas del agente | 146 (76 seguras para el pulso proactivo) |
| Bloques de `state.json` | 33 |
| Sistemas operativos | Windows, Linux, macOS, Android (Termux) |
| Proveedores de modelo | Ollama, OpenRouter, OpenAI-compatible, Puter |
| Dependencias de terceros del core | 0 (con proveedor en la nube) |
