# Attribution

prwrite is our own code. The schema facts it relies on were learned by reading
**py-premiere** (MIT License), vendored at
`plugins/premiere-project-file-assembly/vendor/py-premiere/` (see its `LICENSE`),
and its section papers in `vendor/py-premiere/papers/`.

Facts taken from py-premiere (each cited file:line in `SPEC.md`, "Shared facts"):

- `.prproj` gzip framing: Premiere's 10-byte header (mtime 0, no optional
  fields, OS byte 10), level-6 raw deflate, single member; plain XML also occurs.
- XML style: the declaration `<?xml version="1.0" encoding="UTF-8" ?>`, TAB
  indentation, LF LF after the root, attribute order, which empty elements are
  self-closing, the entity forms used for CR/LF/TAB.
- The object model: `PremiereData Version="3"` root, `ObjectID`/`ObjectUID`
  definitions, `ObjectRef`/`ObjectURef` references, nested private id scopes,
  `BinaryHash` store-once payloads.
- Ticks per second (254016000000) and the minimum object set of a new project.

No function in prwrite is copied or renamed from py-premiere. Our design
differs: byte-span reuse for untouched elements (instead of re-spelling the
whole tree by rule), one persistent object/reference index, and a capped inflate.

py-premiere is Copyright (c) its authors and is used under the MIT License.
