import logging
import os
import subprocess
import time

import websocket
from deployer.env.go import GoEnvironment


class Builder(object):
    """Build out the system-level environment needed to run tests"""

    def __init__(self, config, options):
        self.config = config
        self.options = options
        self.environment = None
        self.env_name = None
        if options:
            self.env_name = options.environment
            if self.env_name:
                self.environment = GoEnvironment(self.env_name)

    def bootstrap(self):
        if not self.environment:
            return
        logging.debug("Bootstrap environment: %s" % self.env_name)
        if self.options.dryrun:
            return
        ec = subprocess.call(['juju', 'status', '-e', self.env_name],
                             stdout=open('/dev/null', 'w'),
                             stderr=subprocess.STDOUT)

        if ec != 0:
            if self.config.bootstrap is True:
                logging.info("Bootstrapping Juju Environment...")
                if self.options.constraints:
                    logging.debug(
                        "Bootstrap constraints: %s", self.options.constraints)
                self.environment.bootstrap(
                    constraints=self.options.constraints)
                self.environment.connect()
                return True
        else:
            self.environment.connect()

    def deploy(self, bundle):
        result = {
            'returncode': 0
        }
        bundle = bundle or self.options.bundle
        if not bundle:
            return result
        if not os.path.exists(bundle):
            raise OSError("Missing required bundle file: %s" % bundle)
        if self.options.dryrun:
            return result
        cmd = ['juju-deployer']
        if self.options.verbose:
            cmd.append('-Wvd')
        cmd += ['-c', bundle]
        if self.options.deployment:
            cmd.append(self.options.deployment)

        logging.debug("deploy %s", ' '.join(cmd))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        ec = p.wait()
        output = p.stdout.read()
        return {
            'returncode': ec,
            'output': output,
            'executable': cmd
        }

    def destroy(self):
        if self.options.no_destroy is not True:
            subprocess.check_call(['juju', 'destroy-environment',
                                   '-y', self.env_name, '--force'])

    def reset(self):
        if self.environment:
            start, timeout = time.time(), 60
            while True:
                try:
                    self.environment.reset(terminate_machines=True)
                    break
                except Exception as e:
                    if isinstance(e, websocket.WebSocketConnectionClosedException):
                        logging.debug(
                            'Websocket connection closed, reconnecting...')
                        self.environment.connect()
                        continue

                    logging.exception(e)
                    if (time.time() - start) > timeout:
                        raise RuntimeError(
                            'Timeout exceeded. Failed to reset environment '
                            ' in %s seconds.' % timeout)
                    time.sleep(1)
                    logging.debug('Retrying environment reset...')

            # wait for all services to be removed
            logging.debug("Waiting for services to be removed...")
            start, timeout = time.time(), 600
            while True:
                status = self.environment.status()
                if not status.get('services', {}):
                    break
                if (time.time() - start) > timeout:
                    raise RuntimeError(
                        'Timeout exceeded. Failed to destroy all services '
                        ' in %s seconds.' % timeout)
                logging.debug(
                    " Remaining services: %s", status.get("services").keys())
                time.sleep(4)

    def build_virtualenv(self, path):
        subprocess.check_call(['virtualenv', path],
                              stdout=open('/dev/null', 'w'))

    def add_source(self, source):
        subprocess.check_call(['sudo', 'apt-add-repository', '--yes', source])

    def add_sources(self, update=True):
        for source in self.config.sources:
            self.add_source(source)
        if self.config.sources and update:
            self.apt_update()

    def apt_update(self):
        subprocess.check_call(['sudo', 'apt-get', 'update', '-qq'])

    def install_packages(self):
        if not self.config.packages:
            return
        cmd = ['sudo', 'apt-get', 'install', '-qq', '-y']
        cmd.extend(self.config.packages)
        subprocess.check_call(cmd)
