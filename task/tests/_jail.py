"""Execution isolation boundary for the submitted program.

E3 hardening: when the hidden / generalization verifier grades the *agent*
submission, the submitted program (``/app/prepare_facts.py`` or the
``/app/bin`` components) is executed inside a chroot jail that contains only
the submitted program, the generated hidden inputs, a minimal Python runtime,
and empty output/work/state directories. ``/tests`` and ``/solution`` are not
present in the jail, so the submitted program cannot read the verifier's
trusted modules (``hidden_reference.py``, ``hidden_universes.py``), the
verifier source, hidden expected values, or the reference oracle.

Mechanism (strongest available in the grading container):
  * the container has CAP_SYS_CHROOT but not CAP_SYS_ADMIN, so mount
    namespaces / unshare are unavailable and no nested docker socket exists;
  * the jail is therefore a real chroot root with a self-contained Python
    runtime, entered via ``os.chroot`` and immediately dropping to the
    unprivileged ``nobody`` uid/gid so the graded program cannot escape the
    jail or gain any capability back;
  * the graded program is invoked as ``nobody`` with a minimal environment and
    only the paths listed above.

The oracle lifecycle is intentionally NOT jailed: the oracle is the trusted
reference and is allowed to have ``/solution`` available.
"""

import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import sysconfig

_NOBODY = None


def _nobody():
    global _NOBODY
    if _NOBODY is None:
        try:
            entry = pwd.getpwnam("nobody")
            _NOBODY = (entry.pw_uid, entry.pw_gid)
        except KeyError:
            _NOBODY = (65534, 65534)
    return _NOBODY


def _ldd_libs(binary):
    """Absolute paths of the shared libraries a binary depends on."""
    out = subprocess.run(["ldd", binary], capture_output=True, text=True)
    libs = []
    for line in out.stdout.splitlines():
        line = line.strip()
        match = re.search(r"=>\s+(\S+)", line)
        if match:
            libs.append(match.group(1))
        elif line.startswith("/"):
            libs.append(line.split()[0])
    return libs


class Jail(object):
    """A chroot jail root on the host filesystem.

    ``root`` is a real directory on the verifier's filesystem. The graded
    program is executed inside it (``os.chroot(root)`` + privilege drop), so a
    host path ``<root>/output/...`` is seen by the graded program as
    ``/output/...``. The verifier reads produced artifacts from the jail's
    output directory on the host side.
    """

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.interp = os.path.join(os.sep, "usr", "bin", "python3.12")
        self._built = False

    # ------------------------------------------------------------------
    # Runtime construction (once per jail)
    # ------------------------------------------------------------------

    def _copy(self, src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(src):
            link = os.readlink(src)
            if os.path.lexists(dst):
                os.unlink(dst)
            os.symlink(link, dst)
        else:
            shutil.copy2(src, dst)

    def _copy_deref(self, src, dst):
        """Copy a file, resolving symlinks, so the destination is a real file.

        Used for shared libraries: the kernel looks up the ELF interpreter by
        its exact path (e.g. /lib64/ld-linux-x86-64.so.2), so that path must
        resolve to a real file inside the jail even when the host library is a
        symlink chain (e.g. /lib64/ld-linux -> /usr/lib/.../ld-linux).
        """
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.realpath(src), dst)

    def build(self):
        """Populate the jail root with a self-contained Python runtime and the
        mutable skeleton directories the submitted program needs."""
        if self._built:
            return
        root = self.root
        os.makedirs(root, exist_ok=True)
        # pytest temp dirs are mode 0700 by default; the jailed process runs as
        # nobody and must be able to traverse the jail root and every subtree.
        os.chmod(root, 0o755)

        real_interp = os.path.realpath(sys.executable)
        interp_rel = self.interp.lstrip(os.sep)
        self._copy(real_interp, os.path.join(root, interp_rel))
        # /usr/bin/python3 -> python3.12 (some programs look up sys.executable
        # or invoke `python3` by name).
        python3_link = os.path.join(root, "usr", "bin", "python3")
        if os.path.lexists(python3_link):
            os.unlink(python3_link)
        os.symlink(os.path.basename(self.interp), python3_link)

        # Standard library (pure-python + lib-dynload native modules).
        stdlib = sysconfig.get_paths()["stdlib"]
        stdlib_dst = os.path.join(root, stdlib.lstrip(os.sep))
        shutil.copytree(stdlib, stdlib_dst, symlinks=True, dirs_exist_ok=True)
        self._chmod_world_readable(stdlib_dst)

        # Shared libraries of the interpreter (libc, libm, libz, libexpat,
        # libpython if dynamically linked, dynamic loader, ...).
        for lib in _ldd_libs(real_interp):
            if lib and lib.startswith("/"):
                self._copy_deref(lib, os.path.join(root, lib.lstrip(os.sep)))
                self._chmod_world_readable(
                    os.path.dirname(os.path.join(root, lib.lstrip(os.sep))))

        # Basic device nodes (some stdlib imports open /dev/null etc.).
        dev = os.path.join(root, "dev")
        os.makedirs(dev, exist_ok=True)
        for name, major, minor, mode in (
                ("null", 1, 3, 0o666),
                ("zero", 1, 5, 0o666),
                ("random", 1, 8, 0o666),
                ("urandom", 1, 9, 0o666)):
            devnode = os.path.join(dev, name)
            try:
                os.mknod(devnode, stat.S_IFCHR | mode,
                         os.makedev(major, minor))
            except OSError:
                if not os.path.exists(devnode):
                    raise
            os.chmod(devnode, mode)

        # /etc passwd/group so getpwuid/getgrgid work inside the jail.
        etc = os.path.join(root, "etc")
        os.makedirs(etc, exist_ok=True)
        for name in ("passwd", "group"):
            src = os.path.join(os.sep, "etc", name)
            if os.path.exists(src):
                self._copy(src, os.path.join(etc, name))

        # Mutable skeleton dirs.
        for rel in ("tmp", "app", "input", "output", "work", "state"):
            d = os.path.join(root, rel)
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o777 if rel in ("tmp", "output", "work", "state")
                     else 0o755)

        self._built = True

    def reset_mutable(self):
        """Clear the per-run writable dirs so a fresh run starts clean."""
        for rel in ("output", "work", "state"):
            d = os.path.join(self.root, rel)
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o777)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def host(self, rel):
        """Host path for a jail-relative path like ``/output``."""
        return os.path.join(self.root, rel.lstrip(os.sep))

    @staticmethod
    def _chmod_world_readable(path):
        """Ensure nobody can traverse/read a copied subtree."""
        for dirpath, dirnames, filenames in os.walk(path):
            os.chmod(dirpath, 0o755)
            for name in filenames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    continue
                os.chmod(full, 0o644 if not os.access(full, os.X_OK) else 0o755)

    def jail(self, host_path):
        """Jail-relative path for a host path under this jail root."""
        if not host_path.startswith(self.root):
            raise ValueError("%s is not inside jail %s" % (host_path,
                                                           self.root))
        return os.sep + os.path.relpath(host_path, self.root)

    def install_program(self, source_app):
        """Copy the submitted /app program into the jail (read-only)."""
        app_dst = self.host("app")
        if os.path.exists(app_dst):
            shutil.rmtree(app_dst)
        os.makedirs(app_dst)
        for entry in os.listdir(source_app):
            if entry in ("__pycache__", ".pytest_cache", "output", "work",
                         "state", ".git"):
                continue
            src = os.path.join(source_app, entry)
            dst = os.path.join(app_dst, entry)
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, dst, symlinks=True)
            else:
                self._copy(src, dst)
        os.chmod(app_dst, 0o755)
        self._chmod_world_readable(app_dst)

    def install_inputs(self, input_dir):
        """Copy the generated hidden inputs into the jail's /input."""
        input_dst = self.host("input")
        if os.path.exists(input_dst):
            shutil.rmtree(input_dst)
        os.makedirs(input_dst)
        for entry in sorted(os.listdir(input_dir)):
            src = os.path.join(input_dir, entry)
            dst = os.path.join(input_dst, entry)
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, dst, symlinks=True)
            else:
                self._copy(src, dst)
        os.chmod(input_dst, 0o755)
        self._chmod_world_readable(input_dst)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, argv, env, timeout=120):
        """Execute ``argv`` inside the jail as the unprivileged nobody user.

        ``argv[0]`` is an absolute path as seen from inside the jail (e.g.
        ``/usr/bin/python3.12``). ``env`` values must reference jail-internal
        paths (``/input/...``, ``/output/...``, ``/state/...``)."""
        uid, gid = _nobody()

        def _enter():
            os.chroot(self.root)
            os.chdir(os.sep)
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)

        env = dict(env)
        env.setdefault("HOME", os.sep + os.path.join("tmp"))
        env.setdefault("PATH", os.path.join(os.sep, "usr", "bin") +
                       os.pathsep + os.path.join(os.sep, "bin"))
        env.setdefault("TMPDIR", os.path.join(os.sep, "tmp"))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(argv, env=env, preexec_fn=_enter,
                              capture_output=True, timeout=timeout)


def prepare_env_for_jail():
    """Base environment for the jailed process.

    Drops verifier hardening flags (PYTHONSAFEPATH etc.) so the graded program
    runs under normal Python semantics, matching the prior non-jailed path.
    """
    env = {}
    for key, value in os.environ.items():
        if key.startswith("PYTHON") or key in ("PYTHONPATH", "PYTHONHOME",
                                               "PYTHONSAFEPATH",
                                               "PYTHONDONTWRITEBYTECODE"):
            continue
        env[key] = value
    return env
