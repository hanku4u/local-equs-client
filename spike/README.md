# Performance spike (M0 / C0.1)

Throwaway code to validate the stack assumption: real parquet → DuckDB query with
downsampling → PyQtGraph render is snappy enough for the 100-chart scenario.

**Targets** (from `mvp-implementation-plan.md` C0.1):

- ~100 MB representative parquet (~30 sensors, ~1 Hz, several days)
- 8 linked charts at ~2000 points each render in **< 1.5s end-to-end**
- Zoom re-query in **< 500 ms**
- Memory under **500 MB**

**Outcome to record in `spike/results.md`:**
end-to-end timing, zoom timing, peak memory.

If targets aren't met, **stop M0 and reconsider the stack before C0.2**.
