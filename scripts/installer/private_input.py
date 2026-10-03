"""Owner-only binary input handling. No authentication values enter argv/logs."""
import os
import stat

from runtime_config import ConfigError


def private_archive(path):
    if not path:
        raise ConfigError('PRIVATE RUNTIME INPUT REQUIRED: supply SUBSCRIBER_DATABASE_INPUT as an authorized mongodump --archive --gzip file')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise ConfigError('PRIVATE RUNTIME INPUT REQUIRED: cannot safely open the database input') from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ConfigError('PRIVATE RUNTIME INPUT REQUIRED: archive must be an owner-controlled regular file with mode 0600 or stricter')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            content = stream.read()
        if not content.startswith(b'\x1f\x8b'):
            raise ConfigError('PRIVATE RUNTIME INPUT REQUIRED: expected a gzip mongodump archive')
        return content
    finally:
        if fd is not None:
            os.close(fd)


def import_decision(record, binding, input_digest, subscriber_count, account_count):
    """Never repeat a restore or merge an unrecognized existing database."""
    if record:
        if record != {'binding': binding, 'input_sha256': input_digest, 'complete': True}:
            raise ConfigError('Private database receipt differs from this cluster/PVC/input; reconcile explicitly')
        if subscriber_count < 1:
            raise ConfigError('Previously imported private database is incomplete')
        return 'verify'
    if subscriber_count:
        raise ConfigError('PRIVATE RUNTIME INPUT REQUIRED: database is not empty and has no matching import receipt; do not automatically overwrite or merge it')
    return 'restore'


def restore_command(kubeconfig, namespace):
    return ['kubectl', '--kubeconfig', kubeconfig, 'exec', '-i', '-n', namespace,
            'open5gs-mongo-custom-0', '--', 'mongorestore', '--archive', '--gzip',
            '--nsInclude=open5gs.subscribers', '--stopOnError']
