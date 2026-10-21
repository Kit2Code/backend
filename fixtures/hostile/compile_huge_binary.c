/* A compile bomb: a 1 GB initialised array (global, so the compiler can't drop it), so the
 * object file and executable would be 1 GB. Stopped by the compile step's file-size limit: CE,
 * with a capped compile log. */
char a[1 << 30] = {1};
int main(void) { return a[12345]; }
