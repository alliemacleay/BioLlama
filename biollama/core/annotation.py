"""
https://pypi.org/project/pyensembl/
https://grch37.rest.ensembl.org

"""

import numpy as np
import pandas as pd
from pyensembl import EnsemblRelease
import requests
import json
import sys
from . import get_pos_flds, exon_df_from_ref


class LlamaEnsembl(object):
    """ Ensembl tools """
    def __init__(self, genome='hg19'):
        if genome == 'hg19':
            self.version = 75
            self.rest_url = "http://grch37.rest.ensembl.org"
        else:
            self.version = 77
            self.rest_url = "http://rest.ensembl.org"
        self.db = EnsemblRelease(self.version)

    def rest_call(self, ext, data=None):
        if data:
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            r = requests.post(self.rest_url + ext, headers=headers, data=data)
        else:
            headers = {"Content-Type": "application/json"}
            r = requests.get(self.rest_url + ext, headers=headers)

        if not r.ok:
            r.raise_for_status()
            sys.exit()

        decoded = r.json()
        # print(repr(decoded))
        return decoded

    def load_ensembl_ref(self, rid=None):
        """ Download, load, and index ensembl data """
        self.db.download(self.version)
        self.db.index()
        if rid is not None:
            return self.db.transcript_by_id(rid)
        else:
            return None

    def get_exon_numbers(self, gene):
        """ This creates exon areas from the biggest transcript """
        dct = {'start': [], 'id': [], 'stop': [], 'transcript': []}
        gene_id = self.db.gene_ids_of_gene_name(gene)[0]
        transcripts = self.db.transcript_ids_of_gene_id(gene_id)
        longest = 0
        e = None
        for trans in transcripts:
            tsc = self.db.exon_ids_of_transcript_id(trans)
            tsize = len(tsc)
            if tsize > longest:
                longest = tsize
                e = tsc
                longest_transcript = trans
        for exid in e:
            exon = self.db.exon_by_id(exid)
            dct['start'].append(exon.start)
            dct['stop'].append(exon.end)
            dct['id'].append(exid)
            dct['transcript'].append(longest_transcript)
        df = pd.DataFrame(dct)
        df['number'] = df.index + 1
        return df

    def get_genes(self, chrom, start, stop):
        if isinstance(chrom, str):
            chrom = chrom.replace('chr', '')
        return [gobj.gene_name for gobj in self.db.genes_at_locus(chrom, start, stop)]

    def get_gene_pos(self, gene):
        gene_id = self.db.gene_ids_of_gene_name(gene)[0]
        result = self.db.gene_by_id(gene_id)
        return result.contig, result.start, result.end

    # Rest client calls
    def get_rsids(self, rsids):
        ext = "/variation/homo_sapiens"
        data = {"ids": rsids}
        return self.rest_call(ext, json.dumps(data))

    def get_cds_region(self, transcript, position):
        """ get location of variant to """
        ext = "/variation/human/{}:{}?".format(transcript, position)
        try:
            mappings = self.rest_call(ext)['mappings'][0]
        except requests.exceptions.HTTPError:
            return '', '', ''
        return mappings['seq_region_name'], mappings['start'], mappings['end']

    def parse_ref_exons(self, chrom, start, stop, gene=None, tx_col=None):
        """ Return fasta reference with only the sequences needed"""
        ens_db = self.db
        if isinstance(chrom, str):
            chrom = chrom.replace('chr', '')
        try:
            exons = ens_db.exons_at_locus(chrom, start, stop)
        except ValueError as e:
            # Load pyensembl db
            raise e
        if not len(exons):
            return '', ''
        exon_numbers = self.get_exon_numbers(exons[0].gene_name)
        transcript = exon_numbers['transcript'].values[0]
        trx_exons = []
        for ex in exons:
            nrow = exon_numbers[exon_numbers['id'] == ex.exon_id]
            if nrow.shape[0] > 0:
                trx_exons.extend(nrow['number'].values)
        return transcript, ','.join([str(number) for number in trx_exons])

    # Annotate DataFrames
    def annotate_dataframe(self, df, chrom_col='CHROM', start_col='START', end_col='END', gene_col=None, tx_col=None):
        genes = []
        exons = []
        transcripts = []
        for i, row in df.iterrows():
            genes_row = self.get_genes(row[chrom_col], row[start_col], row[end_col])
            if gene_col:
                if row[gene_col] in genes_row:
                    genes_row = [row[gene_col]]
                else:
                    print('Warning!! {} not found for {}:{}-{} in row {}'.format(row[gene_col], row[chrom_col], row[start_col], row[end_col], i))
            genes.append(','.join(genes_row))
            if len(genes_row) == 1 or tx_col:
                trans_row, exons_row = self.parse_ref_exons(row[chrom_col], row[start_col], row[end_col], gene=genes_row[0], tx_col=tx_col)  # TODO - add fucntionality to choose gene and transcript
            elif len(genes_row) == 0:
                trans_row, exons_row = self.parse_ref_exons(row[chrom_col], row[start_col], row[end_col], tx_col=tx_col)
            else:
                trans_row = ''
                exons_row = ''
            exons.append(exons_row)
            transcripts.append(trans_row)
        new_df = pd.DataFrame({'genes': genes, 'exons': exons, 'transcript': transcripts}, index=df.index)
        return new_df

    def annotate_variants(self, rsid_array, extra_cols=[]):
        """ Get chom:start-end for a list of variants """
        result = {'chrom': [], 'start': [], 'end': [], 'rsid': [], 'allele': [], 'vartype': [], 'consequence': []}
        for extra in extra_cols:
            result[extra] = []
        response = self.get_rsids(rsid_array)
        for var in rsid_array:
            if var not in response:
                continue
            mapping = response[var]['mappings'][0]
            result['chrom'].append(mapping['seq_region_name'])
            result['start'].append(mapping['start'])
            result['end'].append(mapping['end'])
            result['rsid'].append(var)
            result['allele'].append(mapping['allele_string'])
            result['vartype'].append(response[var]['var_class'])
            result['consequence'].append(response[var]['most_severe_consequence'])
            for extra in extra_cols:
                result[extra].append(response[var][extra])
        return pd.DataFrame(result)

    def annotate_cds_regions(self, df, tx_col='NM', cds_col='MutationName'):
        chroms = []
        starts = []
        ends = []
        for _, row in df.iterrows():
            location = self.get_cds_region(row[tx_col], row[cds_col])
            chroms.append(location[0])
            starts.append(location[1])
            ends.append(location[2])
        df['chrom'] = chroms
        df['start'] = starts
        df['end'] = ends
        return df


class CosmicResult(object):
    def __init__(self, decoded, cosmid):
        self.raw = decoded
        self.internal_id = decoded[0]
        record_names = decoded[1]
        records = decoded[3]
        self.records = {name: record for name, record in zip(record_names, records)}
        self.id = cosmid
        dd = {k: [] for k in ['cosmic_query', 'id', 'gene', 'hgvs', 'hgvs_p']}
        for rec in records:
            dd['cosmic_query'].append(cosmid)
            dd['id'].append(rec[0])
            dd['gene'].append(rec[1])
            dd['hgvs'].append(rec[2])
            dd['hgvs_p'].append(rec[3])
        self.result = pd.DataFrame(dd)


class CosmicResultV3(object):
    def __init__(self, decoded, cosmid):
        self.raw = decoded
        self.internal_id = decoded[0]
        record_names = decoded[1]
        records = decoded[3]
        self.records = {name: record for name, record in zip(record_names, records)}
        self.id = cosmid
        try:
            self.gene = self.records[cosmid][1]
            self.hgvs = self.records[cosmid][2]
            self.hgvs_p = self.records[cosmid][3]
        except KeyError as e:
            print(self.raw)
            raise e

    def __str__(self):
        return "id:{} gene:{} hgvs:{} hgvs_p:{}".format(self.id, self.gene, self.hgvs, self.hgvs_p)


class CosmicLlama(object):
    """ query clintable for cosmic ids.  Supports v3 and v4 from Clintables API"""
    def __init__(self, version='v4'):
        if version.lower() == 'v3':
            self.url = "https://clinicaltables.nlm.nih.gov/api/cosmic/v3/search"
        elif version.lower() == 'v4':
            self.url = "https://clinicaltables.nlm.nih.gov/api/cosmic/v4/search"
        else:
            raise NotImplementedError('Supported versions are "v3" and "v4')
        self.version = version

    def query(self, cosmid):
        """ return CosmicResultV3 object which has hgvs data in the attributes
            or CosmicResult object with .results as dataframe for v4
        """
        qstring = "?terms={}".format(cosmid)
        res = requests.get("{}{}".format(self.url, qstring), headers={"Content-Type": "application/json"})
        if not res.ok:
            res.raise_for_status()
            sys.exit()

        decoded = res.json()
        if self.version == "v3":
            return CosmicResultV3(decoded, cosmid)
        return CosmicResult(decoded, cosmid)


class UCSCResult(object):
    def __init__(self, decoded):
        self.raw = decoded
        ncbi_data = decoded['ncbiRefSeq']
        self.ncbi = {}
        for record in ncbi_data:
            starts = record['exonStarts']
            ends = record['exonEnds']
            strand = record['strand']
            exon_info = exon_df_from_ref(starts, ends, cds_start=record['cdsStart'], cds_end=record['cdsEnd'],
                                         strand=strand)
            self.ncbi[record['name']] = {'gene': record['name2'],
                                         'exon_count': record['exonCount'],
                                         'strand': strand,
                                         'cds_length': exon_info['cds_length'].values[0],
                                         'exon_df': exon_info}

    def longest(self, gene=None):
        """ get longest transcript """
        maxlen = -1
        maxrec = None
        nxm_maxlen = -1
        nxm_maxrec = None
        data = {'transcript': None}
        if gene is not None:
            records = [rec for rec in self.ncbi if self.ncbi[rec]['gene'] == gene]
        else:
            records = self.ncbi
        for record in records:
            if self.ncbi[record]['cds_length'] > maxlen:
                maxrec = record
                maxlen = self.ncbi[record]['cds_length']
            if not record.startswith('XM') and self.ncbi[record]['cds_length'] > nxm_maxlen:
                nxm_maxrec = record
                nxm_maxlen = self.ncbi[record]['cds_length']
        if nxm_maxrec:  # Return longest transcript that isn't XM_ (predicted) transcript
            data = self.ncbi[nxm_maxrec]
            data['transcript'] = nxm_maxrec
        elif maxrec:
            data = self.ncbi[maxrec]
            data['transcript'] = maxrec
        return data

    def genes(self):
        """ get all genes in result """
        return list(set([self.ncbi[res]['gene'] for res in self.ncbi]))

    def df(self):
        dd = {k: [] for k in ['name', 'gene', 'exon_count', 'strand', 'cds_length']}
        for res in self.ncbi:
            dd['name'].append(res)
            dd['gene'].append(self.ncbi['gene'])
            dd['exon_count'].append(self.ncbi['exon_count'])
            dd['strand'].append(self.ncbi['strand'])
            dd['cds_length'].append(self.ncbi['cds_length'])
        return pd.DataFrame(dd)


class UCSCapi(object):
    def __init__(self, genome='hg19'):
        self.url = "https://api.genome.ucsc.edu/getData/track?genome={};track=ncbiRefSeq;".format(genome)

    def query(self, region):
        chrom, start, end = get_pos_flds(region)
        qstring = 'chrom={};start={};end={}'.format(chrom, start, end)
        res = requests.get(self.url + qstring)
        if not res.ok:
            res.raise_for_status()
            sys.exit()

        decoded = res.json()
        return UCSCResult(decoded)

    def annotate_dataframe(self, df):
        dd = {k: [] for k in ['chrom', 'start', 'end', 'gene', 'strand', 'transcript', 'exons']}
        memory = {}
        for i, row in df.iterrows():
            adj_s = 0
            adj_e = 0
            if row['start'] == row['end']:
                adj_s = -1
                adj_e = 1
                print('Warning!!  Same start and end positions.  Use base 0 open end.')
            chrom = row['chrom']
            if not chrom.startswith('chr'):
                chrom = "chr{}".format(chrom)
            ucsc_res = self.query("{}:{}-{}".format(chrom, row['start'] + adj_s, row['end'] + adj_e))
            if 'transcript' not in df.columns:
                gene = None
                transcript = None
                if 'gene' in df.columns:
                    gene = row['gene']
                    if gene in memory:
                        transcript = memory[gene]
                if not transcript:
                    transcript = ucsc_res.longest(gene=gene)['transcript']
            else:
                transcript = row['transcript']
            if transcript:
                gene = ucsc_res.ncbi[transcript]['gene']
                memory[gene] = transcript
                strand = ucsc_res.ncbi[transcript]['strand']
                exon_df = ucsc_res.ncbi[transcript]['exon_df']
                isect = exon_df[(row['start'] <= exon_df['end']) & (exon_df['start'] <= row['end'])]
                if isect.shape[0] > 0:
                    exons = ','.join([str(v) for v in isect['exon_id'].values])
                else:
                    if strand == '+':
                        exons = exon_df[row['start'] > exon_df['end']]
                        if exons.shape[0] > 0:
                            intron = exons.iloc[-1]['exon_id']
                    else:
                        exons = exon_df[row['end'] < exon_df['start']]
                        if exons.shape[0] > 0:
                            intron = exons.iloc[0]['exon_id']
                    exons = 'I{}'.format(intron)
            else:
                exons = ''
                transcript = ''
                strand = ''
            dd['chrom'].append(row['chrom'])
            dd['start'].append(row['start'])
            dd['end'].append(row['end'])
            dd['gene'].append(gene)
            dd['strand'].append(strand)
            dd['transcript'].append(transcript)
            dd['exons'].append(exons)
        return pd.DataFrame(dd)
