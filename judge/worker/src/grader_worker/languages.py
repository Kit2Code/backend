"""The judged languages, with compile and run commands copied from CMS v1.5.1.

Sources: cms/grading/languages/c11_gcc.py, cpp17_gpp.py, cpp20_gpp.py, python3_cpython.py and
CompiledLanguage.get_evaluation_commands in cms/grading/language.py. Every command runs inside an
isolate box, with the box (/box) as the working folder.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    key: str
    name: str
    source_extension: str
    std: str = ""  # for gcc/g++

    def executable_name(self, basename: str) -> str:
        """CMS names the executable after the submission; Python's is a zip archive."""
        return basename + (".zip" if self.key == "python3" else "")

    def compile_commands(self, sources: list[str], executable: str) -> list[list[str]]:
        """The commands that turn `sources` (the main or grader file first) into `executable`."""
        if self.key == "python3":
            # python3_cpython.py: byte-compile everything, the entry point becomes __main__.pyc,
            # and the .pyc files are zipped so `python3 <zip>` runs __main__.
            main_pyc = os.path.splitext(sources[0])[0] + ".pyc"
            others = [os.path.splitext(name)[0] + ".pyc" for name in sources[1:]]
            return [
                ["/usr/bin/python3", "-m", "compileall", "-b", "."],
                ["/bin/mv", main_pyc, "__main__.pyc"],
                ["/usr/bin/zip", executable, "__main__.pyc", *others],
            ]
        compiler = "/usr/bin/gcc" if self.key == "c11" else "/usr/bin/g++"
        command = [compiler, "-DEVAL", f"-std={self.std}", "-O2", "-pipe", "-static", "-s"]
        command += ["-o", executable, *sources]
        if self.key == "c11":
            command.append("-lm")
        return [command]

    def run_command(self, executable: str) -> list[str]:
        if self.key == "python3":
            return ["/usr/bin/python3", executable]
        return [f"./{executable}"]


LANGUAGES: dict[str, Language] = {
    "c11": Language("c11", "C11 / gcc", ".c", std="gnu11"),
    "cpp17": Language("cpp17", "C++17 / g++", ".cpp", std="gnu++17"),
    "cpp20": Language("cpp20", "C++20 / g++", ".cpp", std="gnu++20"),
    "python3": Language("python3", "Python 3 / CPython", ".py"),
}
