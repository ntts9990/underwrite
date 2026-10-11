# Task-scoped comparator for declared Promptfoo 0.123.0 counts.
# Not a full Promptfoo schema validator or an execution/provenance check.
def count:
  type == "number" and . >= 0 and . <= 9007199254740991 and floor == .;
def counts_ok:
  type == "object" and (.successes | count) and (.failures | count) and (.errors | count);
def row_ok:
  type == "object" and (.success | type == "boolean") and
  (.failureReason == 0 or .failureReason == 1 or .failureReason == 2);
def with_total: . + {total:(.successes + .failures + .errors)};
if $format != "promptfoo.eval-output" or $version != "0.123.0" then
  error("UNSUPPORTED_PROFILE")
elif (.results.stats | counts_ok | not) then
  error("INVALID_COUNT_STATS")
elif (.results.results | type) != "array" then
  error("INVALID_ROWS")
elif (.results.results | all(.[]; row_ok) | not) then
  error("INVALID_ROW_FLAGS")
else
  .results as $source |
  ($source.stats | {successes,failures,errors} | with_total) as $reported |
  (reduce $source.results[] as $r ({successes:0,failures:0,errors:0};
    if $r.success then .successes += 1
    elif $r.failureReason == 2 then .errors += 1
    else .failures += 1 end) | with_total) as $rows |
  {producer_stats:$reported,row_tallies:$rows,deltas:{
    successes:($rows.successes-$reported.successes),
    failures:($rows.failures-$reported.failures),
    errors:($rows.errors-$reported.errors),total:($rows.total-$reported.total)}}
end
