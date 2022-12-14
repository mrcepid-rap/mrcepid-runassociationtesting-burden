from os.path import exists

from burden.tool_runners.tool_runner import ToolRunner
from general_utilities.association_resources import *
from general_utilities.linear_model.proccess_model_output import process_staar_outputs
from general_utilities.linear_model.staar_model import staar_null, staar_genes
from general_utilities.thread_utility.thread_utility import *


class STAARRunner(ToolRunner):

    def run_tool(self) -> None:

        # 1. Run the STAAR NULL model
        print("Running STAAR Null Model...")
        staar_null(phenoname=self._association_pack.pheno_names[0],
                   is_binary=self._association_pack.is_binary,
                   found_quantitative_covariates=self._association_pack.found_quantitative_covariates,
                   found_categorical_covariates=self._association_pack.found_categorical_covariates)

        # 2. Run the actual per-gene association tests
        print("Running STAAR masks * chromosomes...")
        thread_utility = ThreadUtility(self._association_pack.threads,
                                       error_message='A STAAR thread failed',
                                       incrementor=10,
                                       thread_factor=1)

        for phenoname in self._association_pack.pheno_names:
            for tarball_prefix in self._association_pack.tarball_prefixes:
                for chromosome in get_chromosomes():
                    if exists(tarball_prefix + "." + chromosome + ".STAAR.matrix.rds"):
                        thread_utility.launch_job(staar_genes,
                                                  tarball_prefix=tarball_prefix,
                                                  chromosome=chromosome,
                                                  phenoname=phenoname,
                                                  has_gene_info=False)

        future_results = thread_utility.collect_futures()

        # 3. Print a preliminary STAAR output
        print("Finalising STAAR outputs...")
        completed_staar_files = []
        # And gather the resulting futures
        for result in future_results:
            tarball_prefix, finished_chromosome, phenoname = result
            completed_staar_files.append(f'{tarball_prefix}.{phenoname}.{finished_chromosome}.STAAR_results.tsv')

        # 4. Annotate and print final STAAR output
        self._outputs.extend(process_staar_outputs(completed_staar_files, self._output_prefix))
