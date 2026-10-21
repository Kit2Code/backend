// A compile bomb: the compiler reads an endless file of random bytes. It must end as CE within
// the compile limits, with a capped compile log.
#include "/dev/urandom"
int main() {}
