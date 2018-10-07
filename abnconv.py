from __future__ import absolute_import, print_function

import argparse
import configparser
import datetime
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree
import zipfile

ns = {'xmlns': "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"}

BEA_re = re.compile("(?P<subtype>[GB])EA.+(\d{2}.){4}\d{2}(?P<payee>.+),PAS(\d+)")

SEPA_re = re.compile("/TRTP/.+")
SEPA_markers_re = re.compile("/(TRTP|CSID|NAME|MARF|REMI|IBAN|BIC|EREF)/")

ABN_re = re.compile("(?P<payee>ABN AMRO Bank N.V.)\s+(?P<memo>\w+).+")

SPAREN_re = re.compile("ACCOUNT BALANCED\s+(?P<memo>CREDIT INTEREST.+)For interest rates")

qif_account_tpl = """!Account
N{name}
T{type}
^"""

qif_tpl_plain_tsx = """!Type:{type}
D{date}
T{amount}
C
P{payee}
M{memo}
L{ledger}
^"""


class Trsx:
    def __init__(self, account_iban):
        self.source_iban = account_iban
        self.dest_iban = None
        self.type = None
        self.date = None
        self.amount = None
        self.payee = None
        self.memo = None
        self.transaction_desc = None

    def __eq__(self, other):
        if type(other) == Trsx:
            return self.source_iban == other.source_iban \
                   and self.dest_iban == other.dest_iban \
                   and self.type == other.type \
                   and self.date == other.date \
                   and self.amount == other.amount \
                   and self.payee == other.payee \
                   and self.memo == other.memo \
                   # and self.transaction_desc == other.transaction_desc
        else:
            return False

    def __hash__(self):
        return hash("%s-%s-%s-%s/%s" %
                    (self.source_iban,
                     self.dest_iban,
                     self.date.strftime("%Y%m%d"),
                     str(self.amount),
                     self.memo))

    def __str__(self):
        return "{dt}: {src} -> {dst} {amt} ({pay}: {memo})".format(dt=self.date.strftime("%d/%m/%Y"),
                                                                   src=self.source_iban,
                                                                   dst=self.dest_iban,
                                                                   amt=self.amount,
                                                                   pay=self.payee,
                                                                   memo=self.memo)

    def is_transfer_transaction(self):
        return self.dest_iban in accounts

    def complementary(self):
        if not self.is_transfer_transaction():
            raise ValueError("Complementary Trsx available only for transfer transactions")

        compl = Trsx(self.dest_iban)
        compl.dest_iban = self.source_iban
        compl.type = self.type
        compl.date = self.date
        compl.amount = self.amount * -1
        compl.payee = self.payee
        compl.memo = self.memo
        compl.transaction_desc = self.transaction_desc
        return compl

    def get_qif_tx(self):
        def nn(v):
            return v if v else ''

        var = {
            'type': self.type,
            'date': self.date.strftime("%Y/%m/%d"),
            'amount': self.amount,
            'payee': nn(self.payee),
            'memo': nn(self.memo),
            'ledger': '',
        }

        if self.is_transfer_transaction():
            if self.memo is None:
                var['memo'] = 'Transfer'

            var['ledger'] = '[%s]' % _get_account(self.dest_iban)

        return qif_tpl_plain_tsx.format(**var)


def _get_account(iban):
    return accounts[iban]


def process_entry(account_iban, elem):
    def find_sepa_field(field):
        if SEPA_re.search(transaction_info):
            start = None
            for marker_match in SEPA_markers_re.finditer(transaction_info):
                if marker_match.group(1) == field:
                    start = marker_match.end(0)
                elif start:
                    return transaction_info[start:marker_match.start(0)]

        return None

    def _get_regex():
        for _type, regexp in {'bea': BEA_re, 'sepa': SEPA_re, 'abn': ABN_re, 'sparen': SPAREN_re}.items():
            _match = regexp.search(transaction_info)
            if _match:
                return _type, _match

        return None, None

    tsx = Trsx(account_iban)

    tsx.date = datetime.datetime.strptime(elem.find("xmlns:ValDt/xmlns:Dt", namespaces=ns).text, "%Y-%m-%d")
    tsx.amount = float(elem.find("xmlns:Amt", namespaces=ns).text)
    if elem.find("xmlns:CdtDbtInd", namespaces=ns).text == 'DBIT':
        tsx.amount *= -1

    transaction_info = elem.find("xmlns:AddtlNtryInf", namespaces=ns).text
    tsx.transaction_desc = transaction_info

    tx_type, match = _get_regex()

    if tx_type == 'bea':
        tsx.type = 'Bank' if match.group("subtype") == 'B' else 'Cash'
        tsx.payee = match.group("payee")
        tsx.memo = transaction_info

    elif tx_type == 'sepa':
        tsx.type = 'Bank'
        tsx.payee = find_sepa_field('NAME')
        tsx.memo = find_sepa_field("REMI")
        tsx.dest_iban = find_sepa_field('IBAN')

    elif tx_type == 'abn':
        tsx.type = 'Bank'
        tsx.payee = match.group("payee")
        tsx.memo = match.group("memo")

    elif tx_type == 'sparen':
        tsx.type = 'Bank'
        tsx.payee = "ABN AMRO Bank N.V."
        tsx.memo = match.group("memo")

    else:
        raise ValueError('Transaction type not supported for "%s"' % transaction_info)

    return tsx


def _qif_account(account_name, account_type):
    return qif_account_tpl.format(name=account_name, type=account_type)


def _trsx_list(file):
    xml_parser = xml.etree.ElementTree.XMLParser(encoding='cp1252')

    def n(name):
        return "{urn:iso:std:iso:20022:tech:xsd:camt.053.001.02}" + name

    if file[-3:] == 'xml':
        tree = xml.etree.ElementTree.parse(file, xml_parser)

        account_iban = tree.find('xmlns:BkToCstmrStmt/xmlns:Stmt/xmlns:Acct/xmlns:Id/xmlns:IBAN', namespaces=ns).text
        for elem in tree.iter(tag=n("Ntry")):
            trsx = process_entry(account_iban, elem)
            if trsx:
                yield trsx
                if trsx.is_transfer_transaction():
                    yield trsx.complementary()
    else:
        raise ValueError('Only CAM.53 XML files are supported')


def _all_files():
    for source in args.source:
        if zipfile.is_zipfile(source):
            tmp_dir = tempfile.mkdtemp(prefix="abnconv_")
            with zipfile.ZipFile(source, 'r') as zf:
                zf.extractall(tmp_dir)

            for _file in [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir)]:
                yield _file

            shutil.rmtree(tmp_dir)
            if args.prune:
                os.remove(source)
        elif os.path.isfile(source) and source[-3:] == 'xml':
            yield source
            if args.prune:
                os.remove(source)


class QIFOutput:
    def __init__(self, output_path):
        self.output_path = output_path
        self.output_file = None
        self.accounts = {}
        self._transaction_list = set()
        self.added = 0
        self.skipped = 0

    def __enter__(self):
        self.output_file = open(self.output_path, 'w')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for qif_entry_list in self.accounts.values():
            for qif_entry in qif_entry_list:
                print(qif_entry, file=self.output_file)

        self.output_file.close()

    def __iadd__(self, transaction: Trsx):
        if transaction not in self._transaction_list:
            self._get_list(transaction.source_iban).append(transaction.get_qif_tx())
            self._transaction_list.add(transaction)
            self.added += 1
        else:
            if args.verbose:
                print("Found duplicated transaction: %s" % transaction)
            self.skipped += 1

        return self

    def _get_list(self, account):
        if account not in self.accounts:
            self.accounts[account] = list()
            self.accounts[account].append(qif_account_tpl.format(name=_get_account(account), type='Bank'))
        return self.accounts[account]


def _load_accounts():
    _accounts = {}
    for account_conf in [conf_parser[section] for section in conf_parser.sections()]:
        _acc_iban = account_conf['iban']
        _accounts[_acc_iban] = account_conf['name'] if 'name' in account_conf else _acc_iban

    return _accounts


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="INI Configuration file")
    parser.add_argument("source", nargs="+", help="ABN AMRO CAMT export file")
    parser.add_argument("--output", help="QIF output file")
    parser.add_argument("--verbose", action='store_true')
    parser.add_argument("--prune", action='store_true', help='Delete original files when conversion is done')

    args = parser.parse_args()

    assert os.path.exists(args.config), "Cannot find configuration file %s" % args.config
    conf_parser = configparser.ConfigParser()
    conf_parser.read(args.config)

    accounts = _load_accounts()

    out_path = args.output if args.output else args.source[0] + '.qif'
    with QIFOutput(out_path) as out:
        for source_file in _all_files():
            for _trsx in _trsx_list(source_file):
                out += _trsx

    print("""
Process completed:
    {inserted} transactions inserted
    into {accounts} accounts
    and {dup} transactions reported as duplicated""".format(inserted=out.added,
                                                            accounts=len(out.accounts),
                                                            dup=out.skipped))
