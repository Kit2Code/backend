# Verdict programs

One small program per verdict and language for the `sum-product` judge test problem
(`fixtures/judge/sum-product`): read a and b, print a + b and a * b. The `.cpp` files are judged
both as C++17 and as C++20.

| File | Expected verdict | What it does |
|---|---|---|
| `ac.*` | AC | prints both answers |
| `pa.*` | PA (0.5) | prints the sum right and the product wrong |
| `wa.*` | WA | prints two wrong numbers |
| `tle.*` | TLE | spins forever on the CPU |
| `mle.*` | MLE | fills 512 MB under a 256 MB limit |
| `re.*` | RE | crashes (bad pointer, uncaught exception, raised error) |
| `ce.*` | CE | doesn't compile |
| `ole.*` | OLE | writes output forever, past the 64 MB file size limit |
