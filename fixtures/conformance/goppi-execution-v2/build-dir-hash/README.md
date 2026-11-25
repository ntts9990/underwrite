# Build dirHash conformance vector

A synthetic vector for the `build_digest` that goppi records for its own build: the in-toto
DigestSet `{"dirHash": "<64 lowercase hex digits>"}` of an installed package directory tree, as
the [paired export](../../../docs/PAIRED_EXPORT.md) document defines it. It is not a goppi
package, a release or evidence of any build.

`vector.json` holds:

- `files`: the name, UTF-8 content and SHA-256 of every file of a small tree shaped like an
  installed goppi 0.1.0 package (`package.json` and `dist/cli.mjs`). The other names make byte
  order matter (`B` sorts before `a-b`, `a.b` and `a/b`) and include a dot file and a later segment
  that starts with `-`.
- `dir_hash`: the expected value. `h1` is the same SHA-256 written as Go's `h1:` base64.
- `archive_target_dir_hash`: the value of a directory that holds `package/`, which is what the
  in-toto command gives when it runs where a tarball was unpacked. It is the dirHash of another
  target, not the value goppi records; a consumer that gets it measured the wrong directory.
- `rejections`: name sets for which goppi defines no value, each with the code goppi refuses it
  with. Every file in them has the empty file's digest, `rejection_file_sha256`.

The expected values were computed with coreutils, not with goppi. Create the tree in a new
directory, then run the in-toto command in it, and once more in its parent for the archive value
(macOS `sha256sum` 1.0 here; `shasum -a 256` gives the same values):

```sh
d=$(mktemp -d)
node -e 'const fs=require("fs"),path=require("path");for(const f of JSON.parse(fs.readFileSync(process.argv[1],"utf8")).files){const t=path.join(process.argv[2],f.name);fs.mkdirSync(path.dirname(t),{recursive:true});fs.writeFileSync(t,f.content)}' vector.json "$d/package"
(cd "$d/package" && find . -type f | cut -c3- | LC_ALL=C sort | xargs -r sha256sum -- | sha256sum | cut -f1 -d' ')
# 4700ec2d5679e5454bf7eaa5eb59429cc0ff452e736f4828bbc91695c64df7d4
(cd "$d" && find . -type f | cut -c3- | LC_ALL=C sort | xargs -r sha256sum -- | sha256sum | cut -f1 -d' ')
# e4187de9434a21aca84991d590fd4ed8efd91048445452db02cd020e385387e0
printf 'h1:%s\n' "$(printf 4700ec2d5679e5454bf7eaa5eb59429cc0ff452e736f4828bbc91695c64df7d4 | xxd -r -p | base64)"
# h1:RwDsLVZ55UVL9+ql61lCnMD/RS5zb0gou8kWlcZN99Q=
```

The command adds `--` after `sha256sum`. The in-toto command without it gives the same values
here, because no name goppi accepts starts with `-` at the top level.

`tests/content-addressing/dir-hash.test.ts` checks these values and refusals against goppi's
implementation, and `tests/application/package-measurement.test.ts` measures the tree installed as
a package. The JSON file is excluded from Biome formatting because its exact bytes are the vector.
