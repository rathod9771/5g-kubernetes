"""Reuse reviewed compile command to build original nine-case regression only."""
import pathlib,shlex,subprocess
s=pathlib.Path('/ci/build_driver.sh').read_text()
command=s[s.index('cc -std='):s.index('\nset +x')].replace('\\\n',' ')
a=shlex.split(command)
a[a.index('/ci/selection_driver.c')]='/native-inputs/regression.c'
a[a.index('/out/selection-driver')]='/out/original-regression'
subprocess.run(a,cwd='/work/source/build',check=True)
