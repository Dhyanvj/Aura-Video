import os
import shutil
import tempfile

from app.utils import utils


class IsolatedStorageDirMixin:
    """
    Redirects utils.storage_dir() to a throwaway temp directory for the
    duration of a test. Without this, any test that reaches
    project_storage.materialize_project() (approving/rejecting/rendering a
    project through the real orchestrator, not just calling a mocked stub)
    writes into the actual repo's storage/projects/ directory - polluting a
    real developer's disk with fake test projects on every run.

    Call _start_isolated_storage_dir() from setUp and
    _stop_isolated_storage_dir() from tearDown.
    """

    def _start_isolated_storage_dir(self) -> None:
        self._storage_tmpdir = tempfile.mkdtemp()
        self._original_storage_dir = utils.storage_dir
        utils.storage_dir = lambda sub_dir="", create=False: self._fake_storage_dir(sub_dir, create)

    def _fake_storage_dir(self, sub_dir: str = "", create: bool = False) -> str:
        d = os.path.join(self._storage_tmpdir, sub_dir) if sub_dir else self._storage_tmpdir
        if create and not os.path.exists(d):
            os.makedirs(d)
        return d

    def _stop_isolated_storage_dir(self) -> None:
        utils.storage_dir = self._original_storage_dir
        shutil.rmtree(self._storage_tmpdir, ignore_errors=True)
