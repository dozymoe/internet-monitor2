""" Internet Monitor module.
"""
import os
import shlex

def monitor(loader, variant=None):
    """ Run internet monitor.
    """
    loader.setup_virtualenv()

    project, variant = loader.setup_project_env('monitor', variant)

    config = loader.config.get('configuration', {})
    config = config.get(variant, {})
    config = config.get(project, {})

    loader.setup_shell_env(config.get('shell_env', {}))

    work_dir = config.get('work_dir', project)
    work_dir = os.path.join(loader.config['work_dir'], work_dir)

    binargs = ['python', 'internet_monitor.py']
    os.chdir(work_dir)
    os.execvp(binargs[0], binargs)


commands = (monitor,)
