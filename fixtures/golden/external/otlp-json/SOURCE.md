# External tool payload example

{
  "name": "otelcol-contrib",
  "version": "0.160.0",
  "release_tag_commit": "e66c67e1d2b9246feeb5beff684c1460f5a96ecb",
  "contrib_tag_commit": "97a2cdd7876501c39b3b7cbac82025b1e41e0282",
  "binary_sha256": "ceb5309ba16f2587dbef765d54e15c803354d038b0495b0b691e1eb9876d17c9",
  "binary_asset": "otelcol-contrib_0.160.0_darwin_arm64.tar.gz",
  "license": "Apache-2.0",
  "producer_sdk": "opentelemetry-sdk==1.44.0 + opentelemetry-exporter-otlp-proto-http==1.44.0 (opentelemetry-proto 1.44.0)",
  "semconv_genai_commit": "0c87594975195608dc91b3f702e250a7b240c151",
  "semconv_genai_stability": "development",
  "writer_modules": "go.opentelemetry.io/collector/pdata@v1.66.0, go.opentelemetry.io/proto/otlp@v1.11.0 (from the binary's build info)",
  "field_sets_derived_from": "opentelemetry-proto 1.44.0 descriptors (sender side)"
}

These are sanitized examples derived from tool-shaped outputs. Incidental development labels have been replaced; they are not unchanged vendor exports and do not establish independent provenance. The adjacent observations.json records digests of the included bytes, codec selectors and recipe files. Hashes show byte identity. Source scores remain unverified claims. Recipes illustrate deterministic payload production; they do not recreate historical identifiers or timestamps.

```
name: otelcol-contrib
version: 0.160.0
release_tag_commit: e66c67e1d2b9246feeb5beff684c1460f5a96ecb
contrib_tag_commit: 97a2cdd7876501c39b3b7cbac82025b1e41e0282
binary_sha256: ceb5309ba16f2587dbef765d54e15c803354d038b0495b0b691e1eb9876d17c9
binary_asset: otelcol-contrib_0.160.0_darwin_arm64.tar.gz
license: Apache-2.0
producer_sdk: opentelemetry-sdk==1.44.0 + opentelemetry-exporter-otlp-proto-http==1.44.0 (opentelemetry-proto 1.44.0)
semconv_genai_commit: 0c87594975195608dc91b3f702e250a7b240c151
semconv_genai_stability: development
writer_modules: go.opentelemetry.io/collector/pdata@v1.66.0, go.opentelemetry.io/proto/otlp@v1.11.0 (from the binary's build info)
field_sets_derived_from: opentelemetry-proto 1.44.0 descriptors (sender side)
```
