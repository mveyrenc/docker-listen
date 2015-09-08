#!/usr/bin/env python
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

"""Docker listen and update dnsmasq
"""

import os
import os.path
import sys
import argparse
import ConfigParser
import logging
import pprint

import dpath
from docker import Client

DEFAULTS = {
    'hosts_dir': '/etc/dnsmasq.d/docker-hosts',
    'docker_url': 'unix://var/run/docker.sock',
    'sighup_enabled': 'True',
    'sighup_process_name': 'dnsmasq',
    'log_level': 'WARN'
}

def main(arguments):
    logging.basicConfig(level=logging.INFO)
    # retrieve configuration file with default values
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument('-c', '--config', nargs='?')
    (config_arg, arguments) = config_parser.parse_known_args(arguments)
    defaults = dict(DEFAULTS)
    if config_arg.config is not None:
        if os.path.isfile(config_arg.config):
            try:
                configp = ConfigParser.SafeConfigParser()
                configp.read(config_arg.config)
                defaults.update(dict(configp.items('docker-listen')))
            except Exception:
                logging.exception('File %s can not be read', config_arg.config)
        else:
            logging.warn('File %s can not be read', config_arg.config)
    
    # fix boolean value
    if defaults['sighup_enabled'] in ('True', 'yes', '1'):
        defaults['sighup_enabled'] = True
    else:
        defaults['sighup_enabled'] = False
    # clean process name
    defaults['sighup_process_name'] = defaults['sighup_process_name'].replace('\'', '')

    # retrieve configuration ; configuration file provides defaults values
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-c', '--config', nargs='?', metavar='FILE', help='load configuration from .ini config file (section docker-listen)')
    parser.add_argument('--docker-url', nargs='?', metavar='URL', help='docker socket path (unix://var/run/docker.sock) or docker url')
    parser.add_argument('--sighup-enabled', nargs='?', type=bool, choices=('yes', 'no'), help='sighup process on events ?')
    parser.add_argument('--sighup-process-name', metavar='NAME', nargs='?', help='name of the process to sighup (with killall)')
    parser.add_argument('--hosts-dir', nargs='?', metavar='DIR_PATH', help='directory where hosts files are stored ; all files in this directory will be deleted')
    parser.add_argument('--log-level', nargs='?', choices=('DEBUG', 'INFO', 'WARN', 'ERROR'))
    logging.debug('Using defaults %s', pprint.pformat(defaults))
    parser.set_defaults(**defaults)
    configuration = parser.parse_args(arguments)

    # set logging level and start working
    logging.getLogger('').setLevel(configuration.log_level)
    logging.info('Current configuration : %s', pprint.pformat(configuration))
    
    # check configuration
    if not os.path.isdir(configuration.hosts_dir):
        logging.error('hosts_dir \'%s\' is not a directory. Stopping.', configuration.hosts_dir)
        return 2
    try:
        client = Client(base_url=configuration.docker_url)
        client.ping()
    except Exception:
        logging.exception('Error communicating with docker socket %s. Stopping.', configuration.docker_url)
        return 2
    events = client.events(decode=True)
    os.umask(0000)
    clean_all(configuration)
    init_all(configuration, client)
    try:
        for event in events:
            logging.info(event)
            if event['status'] == 'start':
                handle_start(configuration, client, event)
            elif event['status'] == 'kill':
                handle_stop(configuration, client, event)
    except Exception:
        logging.exception('Error processing docker events. Stopping.')
        return 2
    return 0

def clean_all(configuration):
    try:
        files = os.listdir(configuration.hosts_dir)
        for f in files:
            path = os.path.join(configuration.hosts_dir, f)
            if os.path.isfile(path):
                os.remove(path)
    except Exception:
        logging.exception('Error cleaning %s', configuration.hosts_dir)

def sighup_dnsmasq(configuration):
    if configuration.sighup_enabled:
        os.system('killall -HUP \'%s\'' % (configuration.sighup_process_name, ))

def init_all(configuration, client):
    try:
        containers = client.containers()
        for container in containers:
            inspect = client.inspect_container(container['Id'])
            handle_add_container(configuration, inspect)
        sighup_dnsmasq(configuration)
    except Exception:
        logging.exception('Fails to register already existing containers')

def handle_start(configuration, client, start_event):
    try:
        inspect = client.inspect_container(start_event['id'])
        handle_add_container(configuration, inspect)
        sighup_dnsmasq(configuration)
    except Exception:
        logging.exception('Unexpected error processing %s', pprint.pformat(start_event))

def handle_stop(configuration, client, stop_event):
    try:
        handle_stop_container(configuration, stop_event['id'])
        sighup_dnsmasq(configuration)
    except Exception:
        logging.exception('Unexpected error processing %s', pprint.pformat(start_event))

def handle_stop_container(configuration, container_id):
    try:
        files = os.listdir(configuration.hosts_dir)
        if container_id in files:
            os.remove(os.path.join(configuration.hosts_dir, container_id))
        else:
            logging.warn('Host file not found for container %s ; delete ignored', container_id)
    except Exception:
        logging.exception('Unexpected error deleting host file for container %s', container_id)

def handle_add_container(configuration, container):
        logging.info(pprint.pformat(container))
        try:
            ip_address = dpath.util.get(container, 'NetworkSettings/IPAddress')
            logging.info('IP address : %s', ip_address)
            with open(os.path.join(configuration.hosts_dir, container['Id']), 'w') as f:
                f.write('{1} {0}.docker.openwide.fr\n'.format(dpath.util.get(container, 'Name').replace('/', ''), ip_address))
        except KeyError:
            logging.warn('No IP address on container %s (from %s)', container['Id'], container['Image'])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
