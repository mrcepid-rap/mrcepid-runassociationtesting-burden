from pathlib import Path

from burden.tool_runners.tool_runner import ToolRunner
from general_utilities.association_resources import *
from general_utilities.thread_utility.thread_utility import *


class BOLTRunner(ToolRunner):

    def run_tool(self) -> None:

        # Need to pare down the bgen file to samples being tested
        # Have to do this for all chromosomes and all included tarballs, so going to parallelise:

        # 1. First we need to download / prep the BGEN files we want to run through BOLT
        print("Processing BGEN files for BOLT run...")
        thread_utility = ThreadUtility(self._association_pack.threads,
                                       error_message='A BOLT thread failed',
                                       incrementor=10,
                                       thread_factor=4)

        # The 'poss_chromosomes.txt' has a slightly different format depending on the data-type being used, but
        # generally has a format of <genetics file>\t<fam file>
        with open('poss_chromosomes.txt', 'w') as poss_chromosomes:
            for chromosome in get_chromosomes():
                if self._association_pack.is_dosage:
                    if chromosome in self._association_pack.dosage_dict:
                        thread_utility.launch_job(class_type=self._process_bolt_dosage_file,
                                                  chromosome=chromosome)
                        poss_chromosomes.write(f'/test/{chromosome}.INCLUDE.dosage '
                                               f'/test/{chromosome}.INCLUDE.fam\n')

                else:
                    for tarball_prefix in self._association_pack.tarball_prefixes:
                        if Path(f'{tarball_prefix}.{chromosome}.BOLT.bgen').exists():
                            poss_chromosomes.write(f'/test/{tarball_prefix}.{chromosome}.bgen '
                                                   f'/test/{tarball_prefix}.{chromosome}.sample\n')
                            thread_utility.launch_job(class_type=self._process_bolt_bgen_file,
                                                      tarball_prefix=tarball_prefix,
                                                      chromosome=chromosome)

                    if self._association_pack.run_marker_tests:
                        poss_chromosomes.write(f'/test/{chromosome}.markers.bgen '
                                               f'/test/{chromosome}.markers.bolt.sample\n')
                        # This makes use of a utility class from AssociationResources since bgen filtering/processing is
                        # IDENTICAL to that done for SAIGE. Do not want to duplicate code!
                        thread_utility.launch_job(class_type=process_bgen_file,
                                                  chrom_bgen_index=self._association_pack.bgen_dict[chromosome],
                                                  chromosome=chromosome)

            poss_chromosomes.close()
            thread_utility.collect_futures()

        # 2. Actually run BOLT
        print("Running BOLT...")
        self._run_bolt()

        # 3. Process the outputs
        print("Processing BOLT outputs...")
        if self._association_pack.is_dosage:
            self._outputs.append(f'{self._output_prefix}.dosage.stats.gz')
        else:
            self._outputs.extend(self._process_bolt_outputs())

    def _process_bolt_dosage_file(self, chromosome: str) -> None:

        current_file_pack = self._association_pack.dosage_dict[chromosome]

        # Read the sample file:
        dosage_sample_list = []
        with current_file_pack['sample'].open('r') as sample_file:
            for line in sample_file:
                sample = line.rstrip().split('\t')
                dosage_sample_list.append(sample[1])

        # Read genotypes as a pandas DF, so we can easily drop columns
        current_genotypes = pd.read_csv(current_file_pack['dosage'], sep="\t",
                                        names=['rsID', 'chrom', 'pos', 'REF', 'ALT'] + dosage_sample_list,
                                        dtype={'chrom': str})

        # Then use the SAMPLES_Include.txt file to get the samples we want to keep:
        valid_sample_list = set()
        with Path('SAMPLES_Include.txt').open('r') as sample_file:
            for line in sample_file:
                sample = line.rstrip()
                valid_sample_list.add(sample)

        # ... and exclude all samples (drop_samples) that we DON'T find in the include file
        drop_samples = []
        for sample in dosage_sample_list:
            if sample not in valid_sample_list:
                drop_samples.append(sample)

        # And finally write the new genotype and sample files:
        with Path(f'{chromosome}.INCLUDE.dosage').open('w') as new_dosage,\
                Path(f'{chromosome}.INCLUDE.fam').open('w') as new_fam:

            current_genotypes = current_genotypes.drop(columns=drop_samples)
            current_genotypes.to_csv(new_dosage, sep="\t", float_format='%0.4f', na_rep='-9', index=False, header=False)

            sample_names = list(current_genotypes.columns)[5:]
            for i in range(0, len(sample_names)):
                new_fam.write(f'{sample_names[i]}\t{sample_names[i]}\n')

            new_dosage.close()
            new_fam.close()

    # This handles processing of mask and whole-exome bgen files for input into BOLT
    @staticmethod
    def _process_bolt_bgen_file(tarball_prefix: str, chromosome: str) -> None:

        # Do the mask first...
        # We need to modify the bgen file to have an alternate name for IDing masks
        cmd = f'plink2 --threads 4 --bgen /test/{tarball_prefix}.{chromosome}.BOLT.bgen \'ref-last\' ' \
                    f'--out /test/{tarball_prefix}.{chromosome} ' \
                    f'--make-just-pvar'
        run_cmd(cmd, True)

        with open(f'{tarball_prefix}.{chromosome}.fixer', 'w') as fix_writer:
            pvar_reader = csv.DictReader(open(f'{tarball_prefix}.{chromosome}.pvar', 'r'), delimiter='\t')
            for variant_id in pvar_reader:
                fix_writer.write(f'{variant_id["ID"]} {variant_id["ID"]}-{tarball_prefix}\n')
            fix_writer.close()

        cmd = f'plink2 --threads 4 --bgen /test/{tarball_prefix}.{chromosome}.BOLT.bgen \'ref-last\' ' \
              f'--sample /test/{tarball_prefix}.{chromosome}.BOLT.sample ' \
              f'--update-name /test/{tarball_prefix}.{chromosome}.fixer ' \
              f'--export bgen-1.2 \'bits=\'8 ' \
              f'--out /test/{tarball_prefix}.{chromosome} ' \
              f'--keep-fam /test/SAMPLES_Include.txt'
        run_cmd(cmd, True)

    # Run rare variant association testing using BOLT
    def _run_bolt(self) -> None:

        # See the README.md for more information on these parameters
        cmd = f'bolt ' + \
                f'--bfile=/test/genetics/UKBB_470K_Autosomes_QCd_WBA ' \
                f'--exclude=/test/genetics/UKBB_470K_Autosomes_QCd.low_MAC.snplist ' \
                f'--phenoFile=/test/phenotypes_covariates.formatted.txt ' \
                f'--phenoCol={self._association_pack.pheno_names[0]} ' \
                f'--covarFile=/test/phenotypes_covariates.formatted.txt ' \
                f'--covarCol=sex ' \
                f'--covarCol=wes_batch ' \
                f'--qCovarCol=age ' \
                f'--qCovarCol=age_squared ' \
                f'--qCovarCol=PC{{1:10}} ' \
                f'--covarMaxLevels=110 ' \
                f'--LDscoresFile=BOLT-LMM_v2.4/tables/LDSCORE.1000G_EUR.tab.gz ' \
                f'--geneticMapFile=BOLT-LMM_v2.4/tables/genetic_map_hg19_withX.txt.gz ' \
                f'--numThreads={self._association_pack.threads} ' \
                f'--statsFile=/test/{self._output_prefix}.stats.gz ' \
                f'--verboseStats '

        # I/O for 'imputed' data depends on input format (dosage/bgen), decide that here
        if self._association_pack.is_dosage:
            # Dosage format takes everything on the command-line, cannot supply a file-list like bgen, but can take
            # multiple files as multiple inputs of the same option (--dosageFile). Only needs one Fid/Iid File (so
            # capture the first one we see regardless of which chromosome)
            with Path('poss_chromosomes.txt').open('r') as poss_file:
                dosage_files = []
                fam_file = None
                for file in poss_file:
                    chrom_data = file.rstrip().split(' ')
                    dosage_files.append(chrom_data[0])
                    if fam_file is None:
                        fam_file = chrom_data[1]

            dosage_files = [f'--dosageFile={file}' for file in dosage_files]
            cmd += f'{" ".join(dosage_files)} ' \
                   f'--dosageFidIidFile={fam_file} ' \
                   f'--statsFileDosageSnps=/test/{self._output_prefix}.dosage.stats.gz'

        else:
            cmd += f'--bgenSampleFileList=/test/poss_chromosomes.txt ' \
                   f'--statsFileBgenSnps=/test/{self._output_prefix}.bgen.stats.gz'

        if self._association_pack.is_bolt_non_infinite:
            cmd += ' --lmmForceNonInf'
        else:
            cmd += ' --lmmInfOnly'

        if len(self._association_pack.found_quantitative_covariates) > 0:
            for covar in self._association_pack.found_quantitative_covariates:
                cmd += f' --qCovarCol={covar} '
        if len(self._association_pack.found_categorical_covariates) > 0:
            for covar in self._association_pack.found_categorical_covariates:
                cmd += f' --covarCol={covar} '
        run_cmd(cmd, True, self._output_prefix + '.BOLT.log')

    # This parses the BOLT output file into a useable format for plotting/R
    def _process_bolt_outputs(self) -> List[str]:

        # First read in the BOLT stats file:
        bolt_table = pd.read_csv(f'{self._output_prefix}.bgen.stats.gz', sep="\t")

        # Split the main table into marker and gene tables and remove the larger table
        bolt_table_gene = bolt_table[bolt_table['SNP'].str.contains('ENST')]
        bolt_table_marker = bolt_table[bolt_table['SNP'].str.contains(':')]
        del bolt_table

        # Now process the gene table into a useable format:
        # First read in the transcripts file
        transcripts_table = build_transcript_table()

        # Test what columns we have in the 'SNP' field so we can name them...
        field_names = define_field_names_from_pandas(bolt_table_gene.iloc[0])
        bolt_table_gene[field_names] = bolt_table_gene['SNP'].str.split("-", expand=True)
        bolt_table_gene = bolt_table_gene.drop(columns=['SNP', 'CHR', 'BP', 'ALLELE1', 'ALLELE0', 'GENPOS'])

        # We need to add in an 'AC' column. Pull samples total from the BOLT log file:
        n_bolt = 0
        with open(self._output_prefix + '.BOLT.log', 'r') as bolt_log_file:
            for line in bolt_log_file:
                if 'samples (Nbgen):' in line:
                    n_bolt = int(line.strip('samples (Nbgen): '))
                    break
            bolt_log_file.close()
        # And use them to calculate a MAC
        bolt_table_gene['AC'] = bolt_table_gene['A1FREQ'] * (n_bolt*2)
        bolt_table_gene['AC'] = bolt_table_gene['AC'].round()

        # Now merge the transcripts table into the gene table to add annotation and the write
        bolt_table_gene = pd.merge(transcripts_table, bolt_table_gene, on='ENST', how="left")
        with open(self._output_prefix + '.genes.BOLT.stats.tsv', 'w') as gene_out:
            # Sort by chrom/pos just to be sure...
            bolt_table_gene = bolt_table_gene.sort_values(by=['chrom', 'start', 'end'])

            bolt_table_gene.to_csv(path_or_buf=gene_out, index=False, sep="\t", na_rep='NA')
            gene_out.close()

            # And bgzip and tabix...
            cmd = "bgzip /test/" + self._output_prefix + '.genes.BOLT.stats.tsv'
            run_cmd(cmd, True)
            cmd = "tabix -S 1 -s 2 -b 3 -e 4 /test/" + self._output_prefix + '.genes.BOLT.stats.tsv.gz'
            run_cmd(cmd, True)

        outputs = [self._output_prefix + '.stats.gz',
                   self._output_prefix + '.genes.BOLT.stats.tsv.gz',
                   self._output_prefix + '.genes.BOLT.stats.tsv.gz.tbi',
                   self._output_prefix + '.BOLT.log']

        # And now process the SNP file (if necessary):
        # Read in the variant index (per-chromosome and mash together)
        if self._association_pack.run_marker_tests:
            variant_index = []
            # Open all chromosome indicies and load them into a list and append them together
            for chromosome in get_chromosomes():
                variant_index.append(pd.read_csv(f'filtered_bgen/{chromosome}.filtered.vep.tsv.gz',
                                                 sep="\t",
                                                 dtype={'SIFT': str, 'POLYPHEN': str}))

            variant_index = pd.concat(variant_index)
            variant_index = variant_index.set_index('varID')

            # For markers, we can use the SNP ID column to get what we need
            bolt_table_marker = bolt_table_marker.rename(columns={'SNP': 'varID', 'A1FREQ': 'BOLT_MAF'})
            bolt_table_marker = bolt_table_marker.drop(columns=['CHR', 'BP', 'ALLELE1', 'ALLELE0', 'GENPOS'])
            bolt_table_marker['BOLT_AC'] = bolt_table_marker['BOLT_MAF'] * (n_bolt*2)
            bolt_table_marker['BOLT_AC'] = bolt_table_marker['BOLT_AC'].round()
            bolt_table_marker = pd.merge(variant_index, bolt_table_marker, on='varID', how="left")
            with open(self._output_prefix + '.markers.BOLT.stats.tsv', 'w') as marker_out:
                # Sort by chrom/pos just to be sure...
                bolt_table_marker = bolt_table_marker.sort_values(by=['CHROM', 'POS'])

                bolt_table_marker.to_csv(path_or_buf=marker_out, index=False, sep="\t", na_rep='NA')
                marker_out.close()

                # And bgzip and tabix...
                cmd = "bgzip /test/" + self._output_prefix + '.markers.BOLT.stats.tsv'
                run_cmd(cmd, True)
                cmd = "tabix -S 1 -s 2 -b 3 -e 3 /test/" + self._output_prefix + '.markers.BOLT.stats.tsv.gz'
                run_cmd(cmd, True)

            outputs.extend([self._output_prefix + '.markers.BOLT.stats.tsv.gz',
                            self._output_prefix + '.markers.BOLT.stats.tsv.gz.tbi'])

        return outputs
