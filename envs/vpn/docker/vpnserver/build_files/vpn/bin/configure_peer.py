import argparse
from string import Template
import subprocess
import os
import re
import ipaddress as ip
from typing import Any, Callable

import tabulate

#
# Script for handling wireguard VPN peer configurations
# - launched in `vpnserver` container (Linux), thus can use some os-specific commands
#

# paths templates for config files
template_file = os.path.join(os.sep, 'fedbiomed', 'vpn', 'config_templates', 'config_%s.env')
assign_config_file = os.path.join(os.sep, 'config', 'ip_assign', 'last_ip_assign_%s')
peer_config_folder = os.path.join(os.sep, 'config', 'config_peers')
wg_config_file = os.path.join(os.sep, 'config', 'wireguard', 'wg0.conf')
config_file = 'config.env'

# UID and GID to use when dropping privileges
init_uid = os.geteuid()
if 'CONTAINER_UID' in os.environ:
    container_uid = int(os.environ['CONTAINER_UID'])
else:
    container_uid = init_uid
init_gid = os.getegid()
if 'CONTAINER_GID' in os.environ:
    container_gid = int(os.environ['CONTAINER_GID'])
else:
    container_gid = init_gid



# run `function(args, kwargs)` with privileges of
# `container_uid:container_gid` (temporary drop)
def run_drop_priv(function: Callable, *args, **kwargs) -> Any:
    os.setegid(container_gid)
    os.seteuid(container_uid)

    ret = function(*args, **kwargs)

    os.seteuid(init_uid)
    os.setegid(init_gid)
    return ret

# read a peer config.env file and build a dict from its content
def read_config_file(filepath: str) -> dict:

    with open(filepath, 'r') as f:
        peer_config = dict(
            tuple(line.removeprefix('export').lstrip().split('=', 1))
            for line in map(lambda line: line.strip(" \n"), f.readlines())
            if not line.startswith('#') and not line == '')

    return peer_config

# save a new peer config.env file from a mapping dict
def save_config_file(peer_type: str, peer_id: str, mapping: dict):

    outpath = os.path.join(peer_config_folder, peer_type)
    run_drop_priv(os.makedirs, outpath, exist_ok=True)

    outpath = os.path.join(outpath, peer_id)
    run_drop_priv(os.mkdir, outpath)

    filepath = os.path.join(outpath, config_file)
    f_config = run_drop_priv(open, filepath, 'w')

    with open(template_file % peer_type, 'r') as f_template:
        f_config.write(Template(f_template.read()).substitute(mapping))
    f_config.close()

    print(f"info: configuration for {peer_type}/{peer_id} saved in {filepath}")

# save wireguard config file from current wireguard interface params
def save_wg_file():

    subprocess.run(
        ["bash", "-c", f"(umask 0077; wg showconf wg0 > {wg_config_file})"])


# generate and save configuration for a new peer
def genconf(peer_type, peer_id):
    assert peer_type == "researcher" or peer_type == "node" or peer_type == "management"

    # wireguard keys
    peer_psk = subprocess.run(
        ["wg", "genpsk"],
        stdout=subprocess.PIPE, text=True).stdout.rstrip('\n')
    server_public_key = subprocess.run(
        ["wg", "show", "wg0", "public-key"],
        stdout=subprocess.PIPE, text=True).stdout.rstrip('\n')

    vpn_net = ip.IPv4Interface(f"{os.environ['VPN_IP']}/{os.environ['VPN_SUBNET_PREFIX']}").network
    assign_net = ip.IPv4Network(os.environ[f"VPN_{peer_type.upper()}_IP_ASSIGN"])

    assign_file = assign_config_file % peer_type
    if os.path.exists(assign_file) and os.path.getsize(assign_file) > 0:
        f = run_drop_priv(open, assign_file, 'r+')

        # assign the next available ip to the peer
        peer_addr_ip = ip.IPv4Address(f.read()) + 1
        f.seek(0)
    else:
        f = run_drop_priv(open, assign_file, 'w')

        peer_addr_ip = assign_net.network_address + 2

    f.write(str(peer_addr_ip))
    f.close()


    assert peer_addr_ip in assign_net
    assert peer_addr_ip in vpn_net

    # create peer configuration
    mapping = {
        "vpn_ip": peer_addr_ip,
        "vpn_subnet_prefix": os.environ['VPN_SUBNET_PREFIX'],
        "vpn_server_endpoint": f"{os.environ['VPN_SERVER_PUBLIC_ADDR']}:{os.environ['VPN_SERVER_PORT']}",
        "vpn_server_allowed_ips": str(vpn_net),
        "vpn_server_public_key": server_public_key,
        "vpn_server_psk": peer_psk,
        "fedbiomed_id": peer_id,
        "fedbiomed_net_ip": os.environ['VPN_IP']
    }

    assert None not in mapping.values()
    assert "" not in mapping.values()

    save_config_file(peer_type, peer_id, mapping)


def add(peer_type, peer_id, peer_public_key):
    assert peer_type == "researcher" or peer_type == "node" or peer_type == "management"

    filepath = os.path.join(peer_config_folder, peer_type, peer_id, config_file)
    peer_config = read_config_file(filepath)

    # apply the config to the server
    subprocess.run(
        ["wg", "set", "wg0", "peer", peer_public_key, "allowed-ips",
            str(ip.IPv4Network(f"{peer_config['VPN_IP']}/32")),
            "preshared-key", "/dev/stdin"],
        text=True,
        input=peer_config['VPN_SERVER_PSK']) 
    save_wg_file()


def remove(peer_type, peer_id, removeconf: bool = False):
    assert peer_type == "researcher" or peer_type == "node" or peer_type == "management"

    filepath = os.path.join(peer_config_folder, peer_type, peer_id, config_file)
    peer_config = read_config_file(filepath)

    f = os.popen('wg show wg0 allowed-ips')
    for line in f:
        peer = re.split('\s+', line.strip(" \n"))
        if peer[1] == str(ip.IPv4Network(f"{peer_config['VPN_IP']}/32")):
            subprocess.run(["wg", "set", "wg0", "peer", peer[0], "remove"])
            print(f"info: removed peer {peer[0]}")
    f.close()

    save_wg_file()

    if removeconf is True:
        conf_dir = os.path.join(peer_config_folder, peer_type, peer_id)
        conf_file = os.path.join(conf_dir, config_file)
        if os.path.isdir(conf_dir) and os.path.isfile(conf_file):
            run_drop_priv(os.remove, conf_file)
            run_drop_priv(os.rmdir, conf_dir)
            print(f"info: removed config dir {conf_dir}")
        else:
            print("CRITICAL: missing configuration file {conf_file}")
            exit(1)


def list():

    # peers = {
    #   IP_prefix_1 = {
    #       'name' = str(name_of_peer_1)
    #       'publickeys = [ str(peer_1_key_A), ... ]
    #   ...
    #   }
    # }
    peers = {}

    # scan peer config files
    for peer_type in os.listdir(peer_config_folder):
        for peer_id in os.listdir(os.path.join(peer_config_folder, peer_type)):

            filepath = os.path.join(peer_config_folder, peer_type, peer_id, config_file)
            peer_config = read_config_file(filepath)

            peer_tmpconf = { 'type': peer_type, 'id': peer_id }
            peer_tmpconf['publickeys'] = []
            peers[str(ip.IPv4Network(f"{peer_config['VPN_IP']}/32"))] = peer_tmpconf

    # scan active peers list

    # (partial) same as `remove` - to be factored
    f = os.popen('wg show wg0 allowed-ips')
    for line in f:
        peer_declared = re.split('\s+', line.strip(" \n"))

        for pkey, pval in peers.items():
            if pkey == peer_declared[1]:
                pval['publickeys'].append(peer_declared[0])
                break
        if not peer_declared[1] in peers:
            peer_tmpconf = { 'type': '?', 'id': '?' }
            peer_tmpconf['publickeys'] = [ peer_declared[0] ]
            peers[peer_declared[1]] = peer_tmpconf
    f.close()

    # display result
    pretty_peers = [[v['type'], v['id'], k, v['publickeys']] for k, v in peers.items()]
    print(tabulate.tabulate(pretty_peers, headers = ['type', 'id', 'prefix', 'peers']))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Configure Wireguard peers on the server")
    subparsers = parser.add_subparsers(dest='operation', required=True, help="operation to perform")

    parser_genconf = subparsers.add_parser("genconf", help="generate the config file for a new peer")
    parser_genconf.add_argument(
        "type",
        choices=["researcher", "node", "management"],
        help="type of client to generate config for")
    parser_genconf.add_argument("id", type=str, help="id of the new peer")


    parser_add = subparsers.add_parser("add", help="add a new peer")
    parser_add.add_argument(
        "type",
        choices=["researcher", "node", "management"],
        help="type of client to add")
    parser_add.add_argument("id", type=str, help="id of the client")
    parser_add.add_argument("publickey", type=str, help="publickey of the client")

    parser_remove = subparsers.add_parser("remove", help="remove a peer")
    parser_remove.add_argument(
        "type",
        choices=["researcher", "node", "management"],
        help="type of client to remove")
    parser_remove.add_argument("id", type=str, help="id client to remove")

    parser_remove = subparsers.add_parser("removeconf", help="remove a peer and its config file")
    parser_remove.add_argument(
        "type",
        choices=["researcher", "node", "management"],
        help="type of client to remove")
    parser_remove.add_argument("id", type=str, help="id client to remove")

    parser_list = subparsers.add_parser("list", help="list peers and config files")

    args = parser.parse_args()

    if args.operation == "genconf":
        genconf(args.type, args.id)
    elif args.operation == "add":
        add(args.type, args.id, args.publickey)
    elif args.operation == "remove":
        remove(args.type, args.id)
    elif args.operation == "removeconf":
        remove(args.type, args.id, True)
    elif args.operation == "list":
        list()
