#!/usr/bin/env python
import sys
import time
import logging
from sets import Set
from os import listdir
from os.path import isfile, join, basename
from argparse import ArgumentParser
from ncclient import manager
from ncclient.operations.rpc import RPCError
from BeautifulSoup import BeautifulSoup
import pyang

#
# The get filter we need to retrieve the schemas a device claims to have
#
schemas_filter = '''<netconf-state xmlns="urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring">
 <schemas/>
</netconf-state>'''

#
# return an etree of the data retrieved
#
def get(m, filter=None):
    if filter and len(filter) > 0:
        c = m.get(filter=('subtree', filter)).data_xml
    else:
        c = m.get().data_xml
    return c


def get_schema(m, schema_list, output_dir, start_after=None):
    failed_download = []
    getting_yet = True
    if start_after:
        getting_yet = False
    for s in schema_list:
        if getting_yet==False:
            if s==start_after:
                getting_yet = True
            else:
                continue
        try:
            c = m.get_schema(s)
            with open(output_dir+'/'+s+'.yang', 'w') as yang:
                print >>yang, BeautifulSoup(
                    c.xml,
                    convertEntities=BeautifulSoup.HTML_ENTITIES).find('data').getText()
                yang.close()
        except RPCError as e:
            # print >>sys.stderr, 'Failed to get schema {} || RPCError: severity={}, tag={}, message={}'.format(
            #     s, e.severity, e.tag, e.message)
            failed_download.append(s)
    return failed_download

if __name__ == '__main__':

    parser = ArgumentParser(description='Provide device and output parameters:')
    parser.add_argument('-a', '--host', type=str, required=True,
                        help="The device IP or DN")
    parser.add_argument('-u', '--username', type=str, default='cisco',
                        help="Go on, guess!")
    parser.add_argument('-p', '--password', type=str, default='cisco',
                        help="Yep, this one too! ;-)")
    parser.add_argument('--port', type=int, default=830,
                        help="Specify this if you want a non-default port")
    parser.add_argument('-o', '--output-dir', type=str, required=False,
                        help="Where to write schema files")
    parser.add_argument('-t', '--timeout', type=int, required=False, default=30,
                        help="Where to write schema files")
    parser.add_argument('-v', '--verbose', action='store_true',
                        help="Do some verbose logging")

    g = parser.add_mutually_exclusive_group()
    g.add_argument('--start-after', type=str, required=False,
                   help="Don't get schemas until after this one")
    g.add_argument('--skip-download', action='store_true', default=False,
                   help="Skip downloading schema and just consider those downloaded already")


    args = parser.parse_args()

    #
    # if you enable verbose logging, it is INCREDIBLY verbose...you
    # have been warned!!
    #
    if args.verbose:
        handler = logging.StreamHandler()
        for l in ['ncclient.transport.ssh', 'ncclient.transport.ssession', 'ncclient.operations.rpc']:
            logger = logging.getLogger(l)
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)

    if not args.output_dir:
        # default the output to got to cwd
        args.output_dir = '.'
        
    #
    # Could use this extra param instead of the last four arguments
    # specified below:
    #
    # device_params={'name': 'iosxr'}
    #
    def iosxr_unknown_host_cb(host, fingerprint):
        return True
    m =  manager.connect(host=args.host,
                         port=args.port,
                         username=args.username,
                         password=args.password,
                         timeout=args.timeout,
                         allow_agent=False,
                         look_for_keys=False,
                         hostkey_verify=False,
                         unknown_host_cb=iosxr_unknown_host_cb)

    #
    # retrieve the schemas datatree and extract all the schema
    # identifiers
    #
    schema_tree = get(m, schemas_filter)
    soup = BeautifulSoup(schema_tree)
    schema_list = [s.getText() for s in soup.findAll('identifier')]

    #
    # Now download all the schema, which also returns a list of any
    # that failed to be downloaded. If we downloaded, list the failed
    # downloads (if any).
    #
    failed_download = []
    if not args.skip_download:
        failed_download = get_schema(m, schema_list, args.output_dir, args.start_after)
        if len(failed_download)>0:
            print "The following schema failed to download:"
            for s in failed_download:
                print '    {}'.format(s)

    #
    # Now let's check all the schema that we downloaded (from this run
    # and any other) and parse them with pyang to extract any imports
    # or includes and verify that they were on the advertised schema
    # list and didn't fail download.
    #
    # TODO: check that filenames have yang extension?
    # TODO: cater for explicitly revisioned imports & includes
    #
    imports_and_includes = Set()
    repos = pyang.FileRepository(args.output_dir)
    yangfiles = [f for f in listdir(args.output_dir) if isfile(join(args.output_dir, f))]
    for fname in yangfiles:
        ctx = pyang.Context(repos)
        fd = open(args.output_dir+'/'+fname, 'r')
        text = fd.read()
        ctx.add_module(fname, text)
        this_module = basename(fname).split(".")[0]
        for ((m,r), module) in ctx.modules.iteritems():
            if m==this_module:
                for s in module.substmts:
                    if (s.keyword=='import') or (s.keyword=='include'):
                        imports_and_includes.add(s.arg)

    #
    # Verify that all imports and includes appeared in the advertised
    # schema
    #
    not_advertised = [i for i in imports_and_includes if i not in schema_list]
    if len(not_advertised)>0:
        print 'The following schema are imported or included, but not advertised:'
        for m in not_advertised:
            print '    {}'.format(m)

    #
    # List out the schema that are imported or included and NOT
    # downloaded successfully.
    #
    not_downloaded = [i for i in imports_and_includes if i in failed_download]
    if len(not_downloaded)>0:
        print 'The following schema are imported or included, but not downloaded:'
        for m in not_downloaded:
            print '    {}'.format(m)
