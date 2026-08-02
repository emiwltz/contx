# Third-party notices

No third-party source code is currently tracked in the CONTX repository.

## OptMem development reference

CONTX is designed to integrate with OptMem, created by Victor Taelin:

- Upstream: <https://github.com/VictorTaelin/OptMem>
- Development snapshot: `1fb164cf39028047781f72ac3bb1e5a691c1dcb0`
- Local reference location: `optmem/` (ignored by the CONTX repository)

The current upstream snapshot does not contain a license file. The local clone
is a development reference and is not part of the CONTX distribution. See
`docs/third-party/optmem.md` for the provenance record and release gate.

## External local model development requirement

The v0 default local model is Gemma 4 E4B QAT, evaluated through Ollama as
`gemma4:e4b-it-qat`.

- Model information: <https://ai.google.dev/gemma/docs/core/model_card_4>
- Ollama model reference: <https://ollama.com/library/gemma4>
- License reported by upstream and the installed artifact: Apache License 2.0

Ollama and the model weights are installed in external user-managed storage.
They are not tracked, bundled, downloaded, exported, or removed by CONTX. A
future distribution that bundles either component must add the complete
applicable notices and revalidate the corresponding artifact and license.

This notice must be updated whenever third-party code, model weights, runtime
binaries, or assets are added to a CONTX source or binary distribution.
