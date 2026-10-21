// A compile bomb: one line that becomes a million errors. The macros repeat `1 = 1;` 2^20 times
// and each one is an error (with notes), so the compiler writes far more than the 64 MB file-size
// limit to stderr. It must end as CE within the compile limits, with a capped compile log.
#define X2(x) x x
#define X4(x) X2(X2(x))
#define X16(x) X4(X4(x))
#define X256(x) X16(X16(x))
int main() { X256(X256(X16(1 = 1;))) }
