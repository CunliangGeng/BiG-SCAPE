#!/usr/bin/env python


"""
BiG-SCAPE

PI: Marnix Medema

Developers:
Marley Yeong                    marleyyeong@live.nl
Jorge Navarro
Emmanuel (Emzo) de los Santos

Dependencies: hmmer, biopython, mafft, munkres

Usage:   Please see `python bigscape.py -h`

Example: python bigscape.py -c 8 --pfam_dir ./ -i ./inputfiles -o ./results

Status: development/testing

"""

import cPickle as pickle  # for storing and retrieving dictionaries
from math import exp, log
import os
import subprocess
import sys
import time
from glob import glob
from itertools import combinations
from collections import defaultdict
from multiprocessing import Pool, cpu_count
from optparse import OptionParser

from Bio import SeqIO
from Bio.SeqFeature import BeforePosition, AfterPosition
from Bio import AlignIO
from Bio import pairwise2
from Bio.SubsMat.MatrixInfo import pam250 as scoring_matrix

from functions import *
from munkres import Munkres



def get_gbk_files(inputdir, min_bgc_size, exclude_gbk_str, gbk_group):
    """Searches given directory for genbank files recursively, will assume that
    the genbank files that have the same name are the same genbank file. 
    Returns a dictionary that contains the names of the clusters found as keys
    and a list that contains [0] a path to the genbank file and [1] the 
    samples that the genbank file is a part of.
    return: {cluster_name:[genbank_path,[s_a,s_b...]]}
    """

    genbankDict = {}

    file_counter = 0
    product_list_per_record = []
    
    print("\nImporting GenBank files")
    if exclude_gbk_str != "":
        print(" Skipping files with '" + exclude_gbk_str + "' in their filename")

    current_dir = ""
    for dirpath, dirnames, filenames in os.walk(inputdir):
        head, tail = os.path.split(dirpath)

        if current_dir != tail:
            current_dir = tail

        genbankfilelist = []

        for fname in filenames:
            if fname[-3:] != "gbk":
                continue
            
            clusterName = fname[:-4]
            
            if exclude_gbk_str != "" and exclude_gbk_str in fname:
                print(" Skipping file " + fname)
                continue
            if "_ORF" in fname:
                print(" Skipping file " + fname + " (string '_ORF' is used internally)")
                continue
            
            if " " in fname:
                sys.exit("\nError: Input GenBank files should not have spaces in their filenames as HMMscan cannot process them properly ('too many arguments').")
                
            
            with open(os.path.join(dirpath, fname), "r") as f:
                try:
                    # basic file verification. Substitutes check_data_integrity
                    records = list(SeqIO.parse(os.path.join(dirpath,fname), "genbank"))
                except ValueError as e:
                    print("   Error with file " + os.path.join(dirpath, fname) + ": \n    '" + str(e) + "'")
                    print("    (This file will be excluded from the analysis)")
                    continue
                else:
                    bgc_size = 0
                    group = "no type"
                    del product_list_per_record[:]
                    
                    record_count = 0
                    for record in records:
                        bgc_size += len(record.seq)
                        record_count += 1
                        
                        for feature in record.features:
                            if "cluster" in feature.type and "product" in feature.qualifiers:
                                if len(feature.qualifiers["product"]) > 1:
                                    print("  WARNING: more than product annotated in record " + str(record_cound) + ", " + fname)
                                    break
                                else:
                                    product_list_per_record.append(feature.qualifiers["product"][0])
                    
                    # check what we have product-wise
                    # In particular, handle different products for multi-record files
                    product_set = set(product_list_per_record)
                    if len(product_set) == 1: # only one type of product
                        group = product_list_per_record[0]
                    elif "other" in product_set: # more than one, and it contains "other"
                        if len(product_set) == 2:
                            group = list(product_set - set(['other']))[0] # group = not "other"
                        else:
                            group = "-".join(product_set - set(['other'])) # likely a hybrid
                    else:
                        group = "-".join(product_set) # likely a hybrid
                    
                    # assuming that the definition field is the same in all records
                    gbk_group[clusterName] = (group, records[0].description)
                    
                    bgc_size = len(record.seq)
                    if bgc_size > min_bgc_size:  # exclude the bgc if it's too small
                        file_counter += 1
                        
                        if clusterName in genbankDict.keys():
                            # current_dir gets to be the name of the sample
                            genbankDict[clusterName][1].add(current_dir) 
                        else:
                            # location of first instance of the file is genbankDict[clustername][0]
                            genbankDict.setdefault(clusterName, [os.path.join(dirpath, fname), set([current_dir])])
                            
                        if verbose:
                            print("  Adding " + fname + " (" + str(bgc_size) + " bps)")
                    else:
                        print(" Discarding " + clusterName +  " (size less than " + str(min_bgc_size) + " bp, was " + str(bgc_size) + ")")
                        
            # else: The file does not have the gbk extension. Skip it
    
    if file_counter == 0:
        sys.exit("\nError: There are no files to process")
        
    if file_counter == 1:
        sys.exit("\nError: Only one file found. Please input at least two files")
    
    print("\n Starting with " + str(file_counter) + " files")

    return genbankDict


def timeit(f):
    def wrap(*args):
        insignificant_runtime = 1 #prevents an overload 
        time1 = time.time()
        ret = f(*args)
        time2 = time.time()
        runtime = time2-time1
        
        runtime_string = '\t%s function took %0.3f s' % (f.func_name, runtime)
        
        if runtime > insignificant_runtime:
            timings_file.write(runtime_string + "\n")
            print runtime_string
            
        return ret
    return wrap


@timeit
def generate_network(cluster_pairs, cores):
    #Contents of the network file: clustername1 clustername2, group1, group2, -log2score, dist, squared similarity
    "saves the distances as the log2 of the similarity"
    pool = Pool(cores, maxtasksperchild=100)
    
    #Assigns the data to the different workers and pools the results back into
    # the network_matrix variable
    network_matrix = pool.map(generate_dist_matrix, cluster_pairs)
    
    # --- Serialized version of distance calculation ---
    # For the time being, use this if you have memory issues
    #network_matrix = []
    #for pair in cluster_pairs:
      #network_matrix.append(generate_dist_matrix(pair))

    # use a dictionary to store results
    network_matrix_dict = {}
    for row in network_matrix:
        network_matrix_dict[row[0], row[1], row[2]] = row[3:]
    
    return network_matrix_dict


def generate_dist_matrix(parms):    
    #Get the values from the parameters
    cluster1 = parms[0]
    cluster2 = parms[1]
    bgc_class = parms[2]
    
    try:
        domain_list_A = DomainList[cluster1]
        domain_list_B = DomainList[cluster2]
    except KeyError:
        print(" Warning: domain list for " + cluster1 + " or " + cluster2 + " was not found. Extracting from pfs files")
        
        cluster_file1 = os.path.join(output_folder, cluster1 + ".pfs")
        cluster_file2 = os.path.join(output_folder, cluster2 + ".pfs")
        
        domain_list_A = get_domain_list(cluster_file1)
        domain_list_B = get_domain_list(cluster_file2)
    
    # this really shouldn't happen if we've filtered domain-less gene clusters already
    if len(domain_list_A) == 0 or len(domain_list_B) == 0:
        print("   Warning: Regarding distance between clusters " + cluster1 + " and " + cluster2 + ":")
        if len(domain_list_A) == 0 and len(domain_list_B) == 0:
            print("   None have identified domains. Distance cannot be calculated")
        elif (domain_list_A) == 0:            
            print("   Cluster " + cluster1 + " has no identified domains. Distance set to 1")
        else:
            print("   Cluster " + cluster2 + " has no identified domains. Distance set to 1")
            
        # last two values (S, Sa) should really be zero but this could give rise to errors when parsing 
        # the network file (unless we catched the case S = Sa = 0
        return [cluster1, cluster2, group_dct[cluster1][0], group_dct[cluster1][1], \
            group_dct[cluster2][0],group_dct[cluster2][1], '0.0', '1.0', '0.0', '0.0', '0.0', '0.0', "1.0", "1.0", "1", "1"] 
    

    dist, jaccard, dds, ai, rDDSna, rDDS, S, Sa = cluster_distance(cluster1, cluster2, domain_list_A, domain_list_B, bgc_class) #sequence dist
        
    if dist == 0:
        logscore = float("inf")
    else:
        logscore = 0
        try:
            logscore = log(dist, 2) #Write exception, ValueError
            logscore = -1.0*logscore
        except ValueError:
            print("ERROR: Unexpected issue when calculating logscore.")
            print(cluster1 + " - " + cluster2 + ": " + str(dist))
            
    #clustername1 clustername2 group1, def1, group2, def2, -log2score, 
    # dist, squared similarity, j, dds, ai
    network_row = [cluster1, cluster2, bgc_class, group_dct[cluster1][0], group_dct[cluster1][1], \
        group_dct[cluster2][0], group_dct[cluster2][1], logscore, dist, (1-dist)**2, \
        jaccard, dds, ai, rDDSna, rDDS, S, Sa]
    
    return network_row
    

def cluster_distance(a, b, a_domlist, b_domlist, bgc_class): 
    """Compare two clusters using information on their domains, and the sequences of the domains"""
    
    Jaccardw, DDSw, AIw, anchorboost = bgc_class_weight[bgc_class]

    temp_domain_fastas = {}
    
    A = a
    B = b
    A_domlist = a_domlist[:]
    B_domlist = b_domlist[:]
    
    setA = set(A_domlist)
    setB = set(B_domlist)
    intersect = setA.intersection(setB)
    
    S = 0
    S_anchor = 0
    
    # Detect totally unrelated pairs from the beginning
    if len(intersect) == 0:
        not_intersect = setA.symmetric_difference(setB)
        
        # Count total number of anchor and non-anchor domain to report in the network file
        # Apart from that, these BGCs are totally unrelated.
        for domain in setA:
            if domain.split(".")[0] in anchor_domains:
                S_anchor += len(BGCs[A][domain])
            else:
                S += len(BGCs[A][domain])
                
        for domain in setB:
            if domain.split(".")[0] in anchor_domains:
                S_anchor += len(BGCs[B][domain])
            else:
                S += len(BGCs[B][domain])
        
        return 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, S, S_anchor

    
    # define the subset of domain sequence tags to include in
    # the DDS calculation. This is done for every domain.
    A_domain_sequence_slice_bottom = defaultdict(int)
    A_domain_sequence_slice_top = defaultdict(int)
    B_domain_sequence_slice_bottom = defaultdict(int)
    B_domain_sequence_slice_top = defaultdict(int)
    
    
    # In metagenomic mode, try to align the shorter BGC ("BGC-fragment") to the
    # best matching slice of the larger BGC
    if metagenomic:
        # BGC A will be the shortest
        if len(a_domlist) < len(b_domlist):
            A = a
            B = b
            A_domlist = a_domlist[:]
            tmpB_domlist = b_domlist[:]
        else:
            A = b
            B = a
            A_domlist = b_domlist[:]
            tmpB_domlist = a_domlist[:]
        
        # Find the slice of the larger BGC where the shorter one fits the best
        setA = set(A_domlist)
        intersect = set()
        startB = 0
        lengthA = len(A_domlist) # length of raw list including copies
        for i in range(len(tmpB_domlist) - lengthA + 1):
            tmpBset = set(tmpB_domlist[i:i+lengthA])
            if len(setA.intersection(tmpBset)) > len(intersect):
                startB = i
                intersect = setA.intersection(tmpBset)
        B_domlist = tmpB_domlist[startB:startB+lengthA]
        setB = set(B_domlist)

        # initialize domain sequence slices
        for domain in setA:
            A_domain_sequence_slice_bottom[domain] = 0
            A_domain_sequence_slice_top[domain] = len(BGCs[A][domain])
            
        # the longest BGC needs to be sliced for domain copies as well
        for i in range(startB):
            domain = tmpB_domlist[i]
            B_domain_sequence_slice_bottom[domain] += 1
            
        # for each top, start at bottom
        for domain in setB:
            B_domain_sequence_slice_top[domain] = B_domain_sequence_slice_bottom[domain]
        for i in range(startB, startB+lengthA):
            domain = tmpB_domlist[i]
            B_domain_sequence_slice_top[domain] += 1

    else:
        # initialize domain sequence slices
        for domain in setA:
            A_domain_sequence_slice_bottom[domain] = 0
            A_domain_sequence_slice_top[domain] = len(BGCs[A][domain])
            
        for domain in setB:
            B_domain_sequence_slice_bottom[domain] = 0
            B_domain_sequence_slice_top[domain] = len(BGCs[B][domain])
        
    
    
    # JACCARD INDEX
    Jaccard = len(intersect)/ float( len(setA) + len(setB) - len(intersect))


    # DDS INDEX
    #domain_difference: Difference in sequence per domain. If one cluster doesn't have a domain at all, but the other does, 
    #this is a sequence difference of 1. If both clusters contain the domain once, and the sequence is the same, there is a seq diff of 0.
    #S: Max occurence of each domain
    domain_difference_anchor,S_anchor = 0,0
    domain_difference,S = 0,0
        
    not_intersect = setA.symmetric_difference(setB)
        
    # Case 1
    for unshared_domain in not_intersect: #no need to look at seq identity, since these domains are unshared
        #for each occurence of an unshared domain do domain_difference += count of domain and S += count of domain
        unshared_occurrences = []

        try:
            unshared_occurrences = BGCs[A][unshared_domain]
        except KeyError:
            unshared_occurrences = BGCs[B][unshared_domain]
            
        # don't look at domain version, hence the split
        if unshared_domain.split(".")[0] in anchor_domains:
            domain_difference_anchor += len(unshared_occurrences)
        else:
            domain_difference += len(unshared_occurrences)
        
    S = domain_difference # can be done because it's the first use of these
    S_anchor = domain_difference_anchor
        
    # Cases 2 and 3 (now merged)
    missing_aligned_domain_files = []
    for shared_domain in intersect:
        specific_domain_list_A = BGCs[A][shared_domain]
        specific_domain_list_B = BGCs[B][shared_domain]
        
        num_copies_a = A_domain_sequence_slice_top[shared_domain] - A_domain_sequence_slice_bottom[shared_domain]
        num_copies_b = B_domain_sequence_slice_top[shared_domain] - B_domain_sequence_slice_bottom[shared_domain]
        
        temp_domain_fastas.clear()
        
        accumulated_distance = 0
            
        # Fill distance matrix between domain's A and B versions
        DistanceMatrix = [[1 for col in range(num_copies_b)] for row in range(num_copies_a)]
        
        for domsa in range(num_copies_a):
            for domsb in range(num_copies_b):
                sequence_tag_a = specific_domain_list_A[domsa + A_domain_sequence_slice_bottom[shared_domain]]
                sequence_tag_b = specific_domain_list_B[domsb + B_domain_sequence_slice_bottom[shared_domain]]
                
                seq_length = 0
                matches = 0
                gaps = 0
                
                try:
                    aligned_seqA = AlignedDomainSequences[sequence_tag_a]
                    aligned_seqB = AlignedDomainSequences[sequence_tag_b]
                    
                except KeyError:
                    # For some reason we don't have the multiple alignment files. 
                    # Try manual alignment
                    if shared_domain not in missing_aligned_domain_files and verbose:
                        # this will print everytime an unfound <domain>.algn is not found for every
                        # distance calculation (but at least, not for every domain pair!)
                        print("  Warning: " + shared_domain + ".algn not found. Trying pairwise alignment...")
                        missing_aligned_domain_files.append(shared_domain)
                    
                    try:
                        unaligned_seqA = temp_domain_fastas[sequence_tag_a]
                        unaligned_seqB = temp_domain_fastas[sequence_tag_b]
                    except KeyError:
                        # parse the file for the first time and load all the sequences
                        with open(os.path.join(domains_folder, shared_domain + ".fasta"),"r") as domain_fasta_handle:
                            temp_domain_fastas = fasta_parser(domain_fasta_handle)
                        
                        unaligned_seqA = temp_domain_fastas[sequence_tag_a]
                        unaligned_seqB = temp_domain_fastas[sequence_tag_b]
                        
                    # gap_open = -15
                    # gap_extend = -6.67. These parameters were set up by Emzo
                    alignScore = pairwise2.align.globalds(unaligned_seqA, unaligned_seqB, scoring_matrix, -15, -6.67, one_alignment_only=True)
                    bestAlignment = alignScore[0]
                    aligned_seqA = bestAlignment[0]
                    aligned_seqB = bestAlignment[1]
                    
                    
                # - Calculate aligned domain sequences similarity -
                # Sequences *should* be of the same length unless something went
                # wrong elsewhere
                if len(aligned_seqA) != len(aligned_seqB):
                    print("\tWARNING: mismatch in sequences' lengths while calculating sequence identity (" + shared_domain + ")")
                    print("\t  Specific domain 1: " + aligned_seqA + " len: " + str(len(aligned_seqA)))
                    print("\t  Specific domain 2: " + aligned_seqB + " len: " + str(len(aligned_seqB)))
                    seq_length = min(len(aligned_seqA), len(aligned_seqB))
                else:
                    seq_length = len(aligned_seqA)
                    
                for position in range(seq_length):
                    if aligned_seqA[position] == aligned_seqB[position]:
                        if aligned_seqA[position] != "-":
                            matches += 1
                        else:
                            gaps += 1
                            
                DistanceMatrix[domsa][domsb] = 1 - ( float(matches)/float(seq_length-gaps) )
                
        #print(shared_domain)
        #for row in DistanceMatrix:
            #print("\t".join(map(str,row)))
        #print("")
        #Only use the best scoring pairs
        Hungarian = Munkres()
        #print "DistanceMatrix", DistanceMatrix
        BestIndexes = Hungarian.compute(DistanceMatrix)
        #print "BestIndexes", BestIndexes
        accumulated_distance = sum([DistanceMatrix[bi[0]][bi[1]] for bi in BestIndexes])
        #print "accumulated_distance", accumulated_distance
        
        # the difference in number of domains accounts for the "lost" (or not duplicated) domains
        sum_seq_dist = (abs(num_copies_a-num_copies_b) + accumulated_distance)  #essentially 1-sim
        normalization_element = max(num_copies_a, num_copies_b)
            
        if shared_domain.split(".")[0] in anchor_domains:
            S_anchor += normalization_element
            domain_difference_anchor += sum_seq_dist
        else:
            S += normalization_element
            domain_difference += sum_seq_dist
        
        
    if S_anchor != 0 and S != 0:
        DDS_non_anchor = domain_difference / float(S)
        DDS_anchor = domain_difference_anchor / float(S_anchor)
        
        # Calculate proper, proportional weight to each kind of domain
        non_anchor_prct = S / float(S + S_anchor)
        anchor_prct = S_anchor / float(S + S_anchor)
        
        # boost anchor subcomponent and re-normalize
        non_anchor_weight = non_anchor_prct / (anchor_prct*anchorboost + non_anchor_prct)
        anchor_weight = anchor_prct*anchorboost / (anchor_prct*anchorboost + non_anchor_prct)

        # Use anchorboost parameter to boost percieved rDDS_anchor
        DDS = (non_anchor_weight*DDS_non_anchor) + (anchor_weight*DDS_anchor)
        
    elif S_anchor == 0:
        DDS_non_anchor = domain_difference / float(S)
        DDS_anchor = 0.0
        
        DDS = DDS_non_anchor
        
    else: #only anchor domains were found
        DDS_non_anchor = 0.0
        DDS_anchor = domain_difference_anchor / float(S_anchor)
        
        DDS = DDS_anchor
 
    DDS = 1-DDS #transform into similarity
 

    # ADJACENCY INDEX
    # calculates the Tanimoto similarity of pairs of adjacent domains
    
    if len(A_domlist) < 2 or len(B_domlist) < 2:
        AI = 0.0
    else:
        setA_pairs = set()
        for l in range(len(A_domlist)-1):
            setA_pairs.add(tuple(sorted([A_domlist[l],A_domlist[l+1]])))
        
        setB_pairs = set()
        for l in range(len(B_domlist)-1):
            setB_pairs.add(tuple(sorted([B_domlist[l],B_domlist[l+1]])))

        # same treatment as in Jaccard
        AI = float(len(setA_pairs.intersection(setB_pairs))) / float(len(setA_pairs.union(setB_pairs)))

    
    # GK INDEX
    #  calculate the Goodman-Kruskal gamma index
    #Ar = [item for item in A_domlist]
    #Ar.reverse()
    #GK = max([calculate_GK(A_domlist, B_domlist, nbhood), calculate_GK(Ar, B_domlist, nbhood)])
    
    Distance = 1 - (Jaccardw * Jaccard) - (DDSw * DDS) - (AIw * AI)
    
    # This could happen due to numerical innacuracies
    if Distance < 0.0:
        if Distance < -0.000001: # this definitely is something else...
            print("Negative distance detected!")
            print(Distance)
            print(A + " - " + B)
            print("J: " + str(Jaccard) + "\tDDS: " + str(DDS) + "\tAI: " + str(AI))
            print("Jw: " + str(Jaccardw) + "\tDDSw: " + str(DDSw) + "\tAIw: " + str(AIw))
        Distance = 0.0
        
    return Distance, Jaccard, DDS, AI, DDS_non_anchor, DDS_anchor, S, S_anchor


"""
*FFT-NS-i (iterative refinement method; two cycles only):
mafft --retree 2 --maxiterate 2 input [> output]
fftnsi input [> output]
*FFT-NS-i (iterative refinement method; max. 1000 iterations):
mafft --retree 2 --maxiterate 1000 input [> output]
*FFT-NS-2 (fast; progressive method):
mafft --retree 2 --maxiterate 0 input [> output]
fftns input [> output]
*FFT-NS-1 (very fast; recommended for >2000 sequences; progressive method with a rough guide tree):
mafft --retree 1 --maxiterate 0 input [> output]
*NW-NS-i (iterative refinement method without FFT approximation; two cycles only):
mafft --retree 2 --maxiterate 2 --nofft input [> output]
nwnsi input [> output]
*NW-NS-2 (fast; progressive method without the FFT approximation):
mafft --retree 2 --maxiterate 0 --nofft input [> output]
nwns input [> output]
*NW-NS-PartTree-1 (recommended for ~10,000 to ~50,000 sequences; progressive method with the PartTree algorithm):
mafft --retree 1 --maxiterate 0 --nofft --parttree input [> output]

With FFT NS 1, a distance matrix is first generated using the 6 tuple score between each pair of sequences both sequences
are scanned from the start for matching 6 tuples, and when a match is found the score is incremented and scanning continues
from the next residue [4]. A guide tree is then constructed by clustering according to these distances, and the sequences 
are then aligned using the branching order of the guide tree. With FFT NS 2, the alignment produced by the FFT NS 1 method
is used to regenerate the distance matrix and the guide tree, and then do a second progressive alignment. In this paper,
FFT NS 1 will be specified whenever distance measures are needed. If no distance measures are required, the default 
FFTNS2 method will be used. 
"""
@timeit
def run_mafft(al_method, maxit, cores, mafft_pars, domain):
    """Runs mafft program with the provided parameters.
    The specific parameters used to run mafft with are actually very important for the final result.
    Using mafft with the most progressive parameters does indeed affect the quality of the distance.
    It is better to just use the domain information for the distance if more computationally intensive options
    for mafft cannot be used. Setting maxiterate to anything higher than 10 did not make a difference in accuracy in the nrps testset"""
    
    alignment_file = domain + ".algn"
    
    
    mafft_cmd_list = []
    mafft_cmd_list.append("mafft") 
    #mafft_cmd_list.append("--distout") #distout will save the distance matrix in a ".hat2" file
    mafft_cmd_list.append("--quiet")
    mafft_cmd_list.append(al_method)
    if maxit != 0:
        mafft_cmd_list.append("--maxiterate " + str(maxit))
        
    mafft_cmd_list.append("--thread")
    mafft_cmd_list.append(str(cores))
    
    if mafft_pars != "":
        mafft_cmd_list.append(mafft_pars)
        
    mafft_cmd_list.append(str(domain) + ".fasta")
    mafft_cmd_list.append(">")
    mafft_cmd_list.append(str(alignment_file))
    
    mafft_cmd = " ".join(mafft_cmd_list)
    
    if verbose:
        print("   " + mafft_cmd)
    subprocess.check_output(mafft_cmd, shell=True)


def launch_hmmalign(cores, domains):
    """
    Launches instances of hmmalign with multiprocessing.
    Note that the domains parameter contains the .fasta extension
    """
    pool = Pool(cores, maxtasksperchild=32)
    pool.map(run_hmmalign, domains)
    pool.close()
    pool.join()
    
def run_hmmalign(domain):
    #domain already contains the full path, with the file extension
    domain_base = domain.split(os.sep)[-1][:-6]
    hmmfetch_pars = ["hmmfetch", os.path.join(pfam_dir,"Pfam-A.hmm.h3m"), domain_base]
    proc_hmmfetch = subprocess.Popen(hmmfetch_pars, stdout=subprocess.PIPE, shell=False)
    
    hmmalign_pars = ["hmmalign", "-o", domain.replace(".fasta",".stk"), "-", domain]
    proc_hmmalign = subprocess.Popen(hmmalign_pars, stdin=proc_hmmfetch.stdout, stdout=subprocess.PIPE, shell=False)
    
    proc_hmmfetch.stdout.close()
    proc_hmmalign.communicate()[0]
    proc_hmmfetch.wait()
    
    if verbose:
        print(" ".join(hmmfetch_pars) + " | " + " ".join(hmmalign_pars))
    
    SeqIO.convert(domain[:-6]+".stk", "stockholm", domain[:-6]+".algn", "fasta")
    

def generateFasta(gbkfilePath, outputdir):
    ## first parse the genbankfile and generate the fasta file for input into hmmscan ##
    outputbase  = gbkfilePath.split(os.sep)[-1].replace(".gbk","")
    if verbose:
        print "   Generating fasta for: ", outputbase
    outputfile = os.path.join(outputdir, outputbase+'.fasta')

    records = list(SeqIO.parse(gbkfilePath, "genbank"))
    cds_ctr = 0
    fasta_data = []
    
    for record in records:
        CDS_List = (feature for feature in record.features if feature.type == 'CDS')

        # parse through the CDS lists to make the fasta file for hmmscan, if translation isn't available attempt manual translation
        for CDS in CDS_List:
            cds_ctr += 1
            
            gene_id = ""
            if "gene" in CDS.qualifiers:
                gene_id = CDS.qualifiers.get('gene',"")[0]
                
            protein_id = ""
            if "protein_id" in CDS.qualifiers:
                protein_id = CDS.qualifiers.get('protein_id',"")[0]
            
            # nofuzzy_start/nofuzzy_end are obsolete
            # http://biopython.org/DIST/docs/api/Bio.SeqFeature.FeatureLocation-class.html#nofuzzy_start
            gene_start = max(0, int(CDS.location.start))
            gene_end = max(0, int(CDS.location.end))
            direction = CDS.location.strand
            
            if direction == 1:
                strand = '+'
            else:
                strand = '-'

            if 'translation' in CDS.qualifiers.keys():
                prot_seq = CDS.qualifiers['translation'][0]
            # If translation isn't available translate manually, this will take longer
            else:
                nt_seq = CDS.location.extract(record.seq)
                
                # If we know sequence is an ORF (like all CDSs), codon table can be
                #  used to correctly translate alternative start codons.
                #  see http://biopython.org/DIST/docs/tutorial/Tutorial.html#htoc25
                # If the sequence has a fuzzy start/end, it might not be complete,
                # (therefore it might not be the true start codon)
                # However, in this case, if 'translation' not availabe, assume 
                #  this is just a random sequence 
                complete_cds = False 
                
                # More about fuzzy positions
                # http://biopython.org/DIST/docs/tutorial/Tutorial.html#htoc39
                fuzzy_start = False 
                if str(CDS.location.start)[0] in "<>":
                    complete_cds = False
                    fuzzy_start = True
                    
                fuzzy_end = False
                if str(CDS.location.end)[0] in "<>":
                    fuzzy_end = True
                
                #for protein sequence if it is at the start of the entry assume 
                # that end of sequence is in frame and trim from the beginning
                #if it is at the end of the genbank entry assume that the start 
                # of the sequence is in frame
                reminder = len(nt_seq)%3
                if reminder > 0:
                    if fuzzy_start and fuzzy_end:
                        print("Warning, CDS (" + outputbase + ", " + CDS.qualifiers.get('locus_tag',"")[0] + ") has fuzzy start and end positions, and a sequence length not multiple of three. Skipping")
                        break
                    
                    if fuzzy_start:
                        if reminder == 1:
                            nt_seq = nt_seq[1:]
                        else:
                            nt_seq = nt_seq[2:]
                    # fuzzy end
                    else:
                        #same logic reverse direction
                        if reminder == 1:
                            nt_seq = nt_seq[:-1]
                        else:
                            nt_seq = nt_seq[:-2]
                
                # The Genetic Codes: www.ncbi.nlm.nih.gov/Taxonomy/Utils/wprintgc.cgi
                CDStable = CDS.qualifiers.get("transl_table", "")[0]
                prot_seq = str(nt_seq.translate(table=CDStable, to_stop=True, cds=complete_cds))
                
            fasta_header = outputbase + "_ORF" + str(cds_ctr)+ ":gid:" + str(gene_id) + ":pid:" + str(protein_id) + ":loc:" + str(gene_start) + ":" + str(gene_end) + ":strand:" + strand
            fasta_header = fasta_header.replace(">","") #the coordinates might contain larger than signs, tools upstream don't like this
            fasta_header = ">"+(fasta_header.replace(" ", "")) #the domtable output format (hmmscan) uses spaces as a delimiter, so these cannot be present in the fasta header
            fasta_data.append((fasta_header, prot_seq))
    
    # write fasta file
    with open(outputfile,'w') as fastaHandle:
        for header_sequence in fasta_data:
            fastaHandle.write('%s\n' % header_sequence[0])
            fastaHandle.write('%s\n' % header_sequence[1])

    return outputfile

def runHmmScan(fastaPath, hmmPath, outputdir, verbose):
    ## will run hmmscan command on a fasta file with a single core and generate a domtable file
    hmmFile = os.path.join(hmmPath,"Pfam-A.hmm")
    if os.path.isfile(fastaPath):
        name = fastaPath.split(os.sep)[-1].replace(".fasta","")
        outputName = os.path.join(outputdir, name+".domtable")
        
        hmmscan_cmd = "hmmscan --cpu 0 --domtblout %s --cut_tc %s %s" % (outputName,hmmFile,fastaPath)
        if verbose == True:
            print("   " + hmmscan_cmd)
        subprocess.check_output(hmmscan_cmd, shell=True)

    else:
        sys.exit("Error running hmmscan: Fasta file " + fastaPath + " doesn't exist")

def parseHmmScan(hmmscanResults, pfd_folder, pfs_folder, overlapCutoff):
    outputbase = hmmscanResults.split(os.sep)[-1].replace(".domtable", "")
    # try to read the domtable file to find out if this gbk has domains. Domains need to be parsed into fastas anyway.
    if os.path.isfile(hmmscanResults):
        pfd_matrix = domtable_parser(outputbase, hmmscanResults)
        num_domains = len(pfd_matrix) # these might still be overlapped, but we need at least 1

        if num_domains > 0:
            print("  Processing domtable file: " + outputbase)

            # check_overlap also sorts the filtered_matrix results
            filtered_matrix, domains = check_overlap(pfd_matrix,overlapCutoff)  #removes overlapping domains, and keeps the highest scoring domain
            
            # Save list of domains per BGC
            pfsoutput = os.path.join(pfs_folder, outputbase + ".pfs")
            with open(pfsoutput, 'wb') as pfs_handle:
                pfs_handle.write(" ".join(domains))
            
            # Save more complete information of each domain per BGC
            pfdoutput = os.path.join(pfd_folder, outputbase + ".pfd")
            with open(pfdoutput,'wb') as pfd_handle:
                write_pfd(pfd_handle, filtered_matrix)
        else:
            # there aren't any domains in this BGC
            # delete from all data structures
            print("  No domains where found in " + outputbase + ".domtable. Removing it from further analysis")
            info = genbankDict.get(outputbase)
            clusters.remove(outputbase)
            baseNames.remove(outputbase)
            gbk_files.remove(info[0])
            for sample in info[1]:
                sampleDict[sample].remove(outputbase)
            del genbankDict[outputbase]
            
    else:
        sys.exit("Error: hmmscan file " + outputbase + " was not found! (parseHmmScan)")

    return("")
   

def CMD_parser():
    parser = OptionParser()
    
    parser.add_option("-o", "--outputdir", dest="outputdir", default="",
                      help="Output directory, this will contain your pfd, pfs, network and hmmscan output files.")
    parser.add_option("-i", "--inputdir", dest="inputdir", default=os.path.dirname(os.path.realpath(__file__)),
                      help="Input directory of gbk files, if left empty, all gbk files in current and lower directories will be used.")
    parser.add_option("-c", "--cores", dest="cores", default=cpu_count(),
                      help="Set the amount of cores the script may use (default: use all available cores)")
    parser.add_option("-v", "--verbose", dest="verbose", action="store_true", default=False,
                      help="Prints more detailed information. Toggle to true.")
    parser.add_option("--include_disc_nodes", dest="include_disc_nodes", action="store_true", default=False,
                      help="Include nodes that have no edges to other nodes from the network. Toggle to activate.")
    parser.add_option("-d", "--domain_overlap_cutoff", dest="domain_overlap_cutoff", default=0.1,
                      help="Specify at which overlap percentage domains are considered to overlap.")
    parser.add_option("-m", "--min_bgc_size", dest="min_bgc_size", default=0,
                      help="Provide the minimum size of a BGC to be included in the analysis. Default is 0 base pairs")
    
    parser.add_option("-s", "--samples", dest="samples", action="store_true", default=False, help="Separate the input files into samples according to their containing folder within the input folder. Toggle to activate")
    
    parser.add_option("--no_all", dest="no_all", action="store_true", default=False, help="By default, BiG-SCAPE uses a single data set comprised of all input files available recursively within the input folder. Toggle to disactivate this behaviour (in that case, if the --samples parameter is not activated, BiG-SCAPE will not create any network file)")
    
    parser.add_option("--mix", dest="mix", action="store_true", default=False, help="By default, BiG-SCAPE separates analysis according to the BGC product (PKS Type I, NRPS, RiPPs, etc.) and will create network directories for each class. Toggle to include an analysis mixing all classes")
    
    parser.add_option("--no_classify", dest="no_classify", action="store_true", default=False, help="By default, BiG-SCAPE classifies the output files analysis based on the BGC product. Toggle to desactivate (in that case, if the --no_classify parameter is not activated, BiG-SCAPE will not create any network file).")
    
    parser.add_option("--banned_classes", dest="banned_classes", default="", help="A comma-separated list of classes that should NOT be included in the classification. Currently: PKSI, PKSother, NRPS, RiPPs, Saccharides, Terpene, PKS-NRP_Hybrids and Others. E.g. \"PKSother, PKS-NRP_Hybrids, Others\"")

    parser.add_option("--metagenomic", dest="metagenomic", action="store_true", default=False, help="Activate Metagenomic mode. BiG-SCAPE will change the logic in the distance calculation phase to try to align shorter, fragmented BGCs.")

    parser.add.option("--hybrids", dest="hybrids", action="store_true", default=False, help="Toggle to also add PKS/NRPS Hybrids to the PKSI, PKSother and NRPS class analysis")

    parser.add_option("--pfam_dir", dest="pfam_dir",
                      default=os.path.dirname(os.path.realpath(__file__)), 
                      help="Location of hmmpress-processed Pfam files. Default is same location of BiG-SCAPE")
    parser.add_option("--anchorfile", dest="anchorfile", default="anchor_domains.txt",
                      help="Provide a custom name for the anchor domains file, default is anchor_domains.txt.")
    parser.add_option("--exclude_gbk_str", dest="exclude_gbk_str", default="",
                      help="If this string occurs in the gbk filename, this file will not be used for the analysis.")
    
    parser.add_option("--mafft_pars", dest="mafft_pars", default="",
                      help="Add single/multiple parameters for MAFFT specific enclosed by quotation marks e.g. \"--nofft --parttree\"")
    parser.add_option("--al_method", dest="al_method", default="--retree 2",
                      help="alignment method for MAFFT, if there's a space in the method's name, enclose by quotation marks. default: \"--retree 2\" corresponds to the FFT-NS-2 method")
    parser.add_option("--maxiterate", dest="maxit", default=1000,
                      help="Maxiterate parameter in MAFFT, default is 1000, corresponds to the FFT-NS-2 method")
    parser.add_option("--mafft_threads", dest="mafft_threads", default=0,
                      help="Set the number of threads in MAFFT, -1 sets the number of threads as the number of physical cores. Default: same as --cores parameter")
    parser.add_option("--use_hmmalign", dest="use_hmmalign", action="store_true", default=False, help="Use hmmalign instead of MAFFT for multiple alignment of domain sequences")
    
    parser.add_option("--force_hmmscan", dest="force_hmmscan", action="store_true", default=False, 
                      help="Force domain prediction using hmmscan even if BiG-SCAPE finds processed domtable files (e.g. to use a new version of PFAM).")
    parser.add_option("--skip_hmmscan", dest="skip_hmmscan", action="store_true", default=False,
                      help="When skipping hmmscan, the GBK files should be available, and the domain tables need to be in the output folder.")
    parser.add_option("--skip_ma", dest="skip_ma", action="store_true", default=False, 
                      help="Skip Multiple Alignment of domains' sequences.")
    parser.add_option("--skip_all", dest="skip_all", action="store_true",
                      default = False, help = "Only generate new network files. ")
    parser.add_option("--cutoffs", dest="cutoffs", default="1",
                      help="Generate networks using multiple raw distance cutoff values, example: \"0.1, 0.25, 0.5, 1.0\". Default: 1.0 (all distances are included)")

    (options, args) = parser.parse_args()
    return options, args


if __name__=="__main__":
    options, args = CMD_parser()
    
    if options.outputdir == "":
        print "please provide a name for an output folder using parameter -o or --outputdir"
        sys.exit(0)
    
    global anchor_domains
    if os.path.isfile(options.anchorfile):
        anchor_domains = get_anchor_domains(options.anchorfile)
    else:
        print("File with list of anchor domains not found")
        anchor_domains = []
    
    global bgc_class_weight
    global AlignedDomainSequences
    global DomainList
    global verbose
    global BGCs
    
    # contains the type of the final product of the BGC (as predicted by AntiSMASH), 
    # as well as the definition line from the BGC file. Used in the final network files.
    global output_folder

    global pfam_dir
    global timings_file
    global cores
    global metagenomic
    
    include_disc_nodes = options.include_disc_nodes
    
    cores = int(options.cores)
    
    options_all = not options.no_all
    options_samples = options.samples
    
    options_mix = options.mix
    options_classify = not options.no_classify
    metagenomic = options.metagenomic
    
    cutoff_list = [float(c.strip()) for c in options.cutoffs.split(",")]
    for c in cutoff_list:
        if c <= 0.0:
            cutoff_list.remove(c)
    if 1.0 not in cutoff_list:
        cutoff_list.append(1.0) # compulsory for re-runs
        print("Adding cutoff=1.0 case by default")
    
    output_folder = str(options.outputdir)
    
    pfam_dir = str(options.pfam_dir)
    h3f = os.path.join(pfam_dir, "Pfam-A.hmm.h3f")
    h3i = os.path.join(pfam_dir, "Pfam-A.hmm.h3i")
    h3m = os.path.join(pfam_dir, "Pfam-A.hmm.h3m")
    h3p = os.path.join(pfam_dir, "Pfam-A.hmm.h3p")
    if not (os.path.isfile(h3f) and os.path.isfile(h3i) and os.path.isfile(h3m) and os.path.isfile(h3p)):
        print("One or more of the necessary Pfam files (.h3f, .h3i, .h3m, .h3p) were not found")
        if os.path.isfile(os.path.join(pfam_dir, "Pfam-A.hmm")):
            print("Please use hmmpress with Pfam-A.hmm")
        else:
            print("Please download the latest Pfam-A.hmm file from http://pfam.xfam.org/")
            print("Then use hmmpress on it, and use the --pfam_dir parameter to point to the location of the files")
        sys.exit()
    
    if options.mafft_threads == 0:
        options.mafft_threads = options.cores
    else:
        options.mafft_threads = int(options.mafft_threads)
                    
    verbose = options.verbose
    
    networks_folder_all = "networks_all"
    networks_folder_samples = "networks_samples"
    
    if options.skip_all:
        if options.skip_hmmscan or options.skip_ma:
            print("Overriding --skip_hmmscan/--skip_ma with --skip_all parameter")
            options.skip_hmmscan = False
            options.skip_ma = False
    
    time1 = time.time()
    print("\n   - - Obtaining input files - -")
    
    # Make the following available for possibly deleting entries within parseHmmScan
    global genbankDict, gbk_files, sampleDict, clusters, baseNames
    
    # genbankDict: {cluster_name:[genbank_path_to_1st_instance,[sample_1,sample_2,...]]}
    group_dct = {} # also 
    genbankDict = get_gbk_files(options.inputdir, int(options.min_bgc_size), options.exclude_gbk_str, group_dct)

    # clusters and sampleDict contain the necessary structure for all-vs-all and sample analysis
    clusters = genbankDict.keys()
    
    sampleDict = {} # {sampleName:set(bgc1,bgc2,...)}
    gbk_files = [] # raw list of gbk file locations
    for (cluster, (path, clusterSample)) in genbankDict.iteritems():
        gbk_files.append(path)
        for sample in clusterSample:
            clustersInSample = sampleDict.get(sample, set())
            clustersInSample.add(cluster)
            sampleDict[sample] = clustersInSample

    
    print("\nCreating output directories")
    
    domtable_folder = os.path.join(output_folder, "domtable")
    bgc_fasta_folder = os.path.join(output_folder, "fasta")
    pfs_folder = os.path.join(output_folder, "pfs")
    pfd_folder = os.path.join(output_folder, "pfd")    
    domains_folder = os.path.join(output_folder, "domains")
    
    create_directory(output_folder, "Output", False)
    write_parameters(output_folder, options)
    
    create_directory(domtable_folder, "Domtable", False)
    create_directory(domains_folder, "Domains", False)
    create_directory(bgc_fasta_folder, "BGC fastas", False)
    create_directory(pfs_folder, "pfs", False)
    create_directory(pfd_folder, "pfd", False)
    

    print("\nTrying threading on %i cores" % cores)
    
    #open the file that will contain the timed functions
    timings_file = open(os.path.join(output_folder, "runtimes.txt"), 'w') 
    
    """BGCs -- 
    dictionary of this structure:
    BGCs = {'cluster_name_x': { 'general_domain_name_x' : ['specific_domain_name_1',
     'specific_domain_name_2'] } }
    - cluster_name_x: cluster name (can be anything)
    - general_domain_name_x: PFAM ID, for example 'PF00550'
    - specific_domain_name_x: ID of a specific domain that will allow to you to map it to names in DMS unequivocally
     (for example, 'PF00550_start_end', where start and end are genomic positions)."""     
    BGCs = {} #will contain the BGCs
    
    
    # Weights in the format J, DDS, AI, anchorboost
    # Generated with optimization results 2016-12-05. 
    # Used the basic list of 4 anchor domains.
    bgc_class_weight = {}
    bgc_class_weight["PKSI"] = (0.22, 0.76, 0.02, 1.0)
    bgc_class_weight["PKSother"] = (0.0, 0.32, 0.68, 4.0)
    bgc_class_weight["NRPS"] = (0.0, 1.0, 0.0, 4.0)
    bgc_class_weight["RiPPs"] = (0.28, 0.71, 0.01, 1.0)
    bgc_class_weight["Saccharides"] = (0.0, 0.0, 1.0, 1.0)
    bgc_class_weight["Terpene"] = (0.2, 0.75, 0.05, 2.0)
    bgc_class_weight["PKS-NRP_Hybrids"] = (0.0, 0.78, 0.22, 1.0)
    bgc_class_weight["Others"] = (0.01, 0.97, 0.02, 4.0)
    
    #define which classes will be analyzed (if in the options_classify mode)
    valid_classes = set()
    for key in bgc_class_weight:
        valid_classes.add(key.lower())
    user_banned_classes = set([a.strip().lower() for a in options.banned_classes.split(",")])
    valid_classes = valid_classes - user_banned_classes
        
    bgc_class_weight["mix"] = (0.2, 0.75, 0.05, 2.0) # default when not separating in classes
    BGC_classes = defaultdict(list)    
    
    AlignedDomainSequences = {} # Key: specific domain sequence label. Item: aligned sequence
    DomainList = {} # Key: BGC. Item: ordered list of domains
    
    # to avoid multiple alignment if there's only 1 seq. representing a particular domain
    sequences_per_domain = {}
    
    print("\n\n   - - Processing input files - -")
    
    # These will be used to track if we drop files in processing
    genbankFileLocations = set(gbk_files)
    baseNames = set(clusters)

    ### Step 1: Generate Fasta Files
    print "\nParsing genbank files to generate fasta files for hmmscan"

    # filter through task list to avoid unecessary computation: 
    #  If the corresponding fasta file from every genbank exists, skip it
    alreadyDone = set()
    for genbank in genbankFileLocations:
        outputbase = genbank.split(os.sep)[-1].replace(".gbk","")
        outputfile = os.path.join(bgc_fasta_folder,outputbase + '.fasta')
        if os.path.isfile(outputfile) and os.path.getsize(outputfile) > 0:
            alreadyDone.add(genbank)

    if len(genbankFileLocations - alreadyDone) == 0:
        print(" All GenBank files had already been processed")
    elif len(alreadyDone) > 0:
        if len(genbankFileLocations - alreadyDone) < 20:
            print " Warning: The following NEW input file(s) will be processed: %s" % ", ".join(x.split(os.sep)[-1].replace(".gbk","") for x in genbankFileLocations - alreadyDone)
        else:
            print(" Warning: " + str(len(genbankFileLocations-alreadyDone)) + " new files will be processed")
    else:
        print(" Processing " + str(len(genbankFileLocations)) + " files")

    # Generate Pool of workers
    pool = Pool(cores,maxtasksperchild=32)
    for genbankFile in (genbankFileLocations - alreadyDone):
        pool.apply_async(generateFasta,args =(genbankFile,bgc_fasta_folder))
    pool.close()
    pool.join()

    print " Finished generating fasta files."

    ### Step 2: Run hmmscan
    print("\nPredicting domains using hmmscan")
    
    # All available fasta files (could be more than it should if reusing output folder)
    allFastaFiles = set(glob(os.path.join(bgc_fasta_folder,"*.fasta")))
    
    # fastaFiles: all the fasta files that should be there 
    # (i.e. correspond to the input files)
    fastaFiles = set()
    for name in baseNames:
        fastaFiles.add(os.path.join(bgc_fasta_folder, name+".fasta"))
    
    # fastaBases: the actual fasta files we have that correspond to the input
    fastaBases = allFastaFiles.intersection(fastaFiles)
    
    # Verify that all input files had their fasta sequences extracted
    if len(fastaFiles - fastaBases) > 0:
        sys.exit("Error! The following files did NOT have their fasta sequences extracted: " + ", ".join(fastaFiles - fastaBases))
    
    # Make a list of all fasta files that need to be processed
    # (i.e., they don't yet have a corresponding .domtable)
    if options.force_hmmscan:
        # process all files, regardless of whether they already existed
        task_set = fastaFiles
        print(" Forcing domain prediction on ALL fasta files (--force_hmmscan)")
    else:
        # find already processed files
        alreadyDone = set()
        for fasta in fastaFiles:
            outputbase  = fasta.split(os.sep)[-1].replace(".fasta","")
            outputfile = os.path.join(domtable_folder,outputbase + '.domtable')
            if os.path.isfile(outputfile) and os.path.getsize(outputfile) > 0:
                alreadyDone.add(fasta)
            
        if len(fastaFiles - alreadyDone) == 0:
            print(" All fasta files had already been processed")
        elif len(alreadyDone) > 0:
            if len(fastaFiles-alreadyDone) < 20:
                print " Warning! The following NEW fasta file(s) will be processed: %s" % ", ".join(x.split(os.sep)[-1].replace(".fasta","") for x in fastaFiles - alreadyDone)
            else:
                print(" Warning: " + str(len(fastaFiles-alreadyDone)) + " NEW fasta files will be processed")
        else:
            print(" Predicting domains for " + str(len(fastaFiles)) + " fasta files")

        task_set = fastaFiles - alreadyDone
        
    pool = Pool(cores,maxtasksperchild=1)
    for fastaFile in task_set:
        pool.apply_async(runHmmScan,args=(fastaFile, pfam_dir, domtable_folder, verbose))
    pool.close()
    pool.join()

    print " Finished generating domtable files."


    ### Step 3: Parse hmmscan domtable results and generate pfs and pfd files
    print("\nParsing hmmscan domtable files")
    
    # All available domtable files
    allDomtableFiles = set(glob(os.path.join(domtable_folder,"*.domtable")))
    
    # domtableFiles: all domtable files corresponding to the input files
    domtableFiles = set()
    for name in baseNames:
        domtableFiles.add(os.path.join(domtable_folder, name+".domtable"))
    
    # domtableBases: the actual set of input files with coresponding domtable files
    domtableBases = allDomtableFiles.intersection(domtableFiles)
    
    # Verify that all input files have a corresponding domtable file
    if len(domtableFiles - domtableBases) > 0:
        sys.exit("Error! The following files did NOT have their domains predicted: " + ", ".join(domtableFiles - domtableBases))
    
    # find already processed files (assuming that if the pfd file exists, the pfs should too)
    alreadyDone = set()
    if not options.force_hmmscan:
        for domtable in domtableFiles:
            outputbase = domtable.split(os.sep)[-1].replace(".domtable","")
            outputfile = os.path.join(pfd_folder, outputbase + '.pfd')
            if os.path.isfile(outputfile) and os.path.getsize(outputfile) > 0:
                alreadyDone.add(domtable)
                
    if len(domtableFiles - alreadyDone) == 0: # Re-run
        print(" All domtable files had already been processed")
    elif len(alreadyDone) > 0: # Incomplete run
        if len(domtableFiles-alreadyDone) < 20:
            print " Warning! The following domtable files had not been processed: %s" % ", ".join(x.split(os.sep)[-1].replace(".domtable","") for x in domtableFiles - alreadyDone)
        else:
            print(" Warning: " + str(len(domtableFiles-alreadyDone)) + " domtable files will be processed")
    else: # First run
        print(" Processing " + str(len(domtableFiles)) + " domtable files")

    # If using the multiprocessing version and outputbase doesn't have any
    #  predicted domains, it's not as easy to remove if from the analysis
    #  (probably because parseHmmScan only has a copy of clusters et al?)
    # Using serialized version for now. Probably doesn't have too bad an impact
    #pool = Pool(cores,maxtasksperchild=32)
    for domtableFile in domtableFiles - alreadyDone:
        parseHmmScan(domtableFile, pfd_folder, pfs_folder, options.domain_overlap_cutoff)
        #pool.apply_async(parseHmmScan, args=(domtableFile,output_folder,options.domain_overlap_cutoff))
    #pool.close()
    #pool.join()
    
    # If number of pfd files did not change, no new sequences were added to the 
    #  domain fastas and we could try to resume the multiple alignment phase
    # baseNames have been pruned of BGCs with no domains that might've been added temporarily
    try_MA_resume = False
    if len(baseNames - set(pfd.split(os.sep)[-1][:-9] for pfd in alreadyDone)) == 0:
        try_MA_resume = True
    else:
        # new sequences will be added to the domain fasta files. Clean domains folder
        for thing in os.listdir(domains_folder):
            os.remove(os.path.join(domains_folder,thing))

    print " Finished generating generating pfs and pfd files."


    ### Step 4: Parse the pfs, pfd files to generate BGC dictionary, clusters, and clusters per sample objects
    print("\nProcessing domains sequence files")
    
    # All available pfd files
    allPfdFiles = set(glob(os.path.join(pfd_folder,"*.pfd")))
    
    # pfdFiles: all pfd files corresponding to the input files
    # (some input files could've been removed due to not having predicted domains)
    pfdFiles = set()
    for name in baseNames:
        pfdFiles.add(os.path.join(pfd_folder, name+".pfd"))
    
    # pfdBases: the actual set of input files that have pfd files
    pfdBases = allPfdFiles.intersection(pfdFiles)
    
    # verify previous step. 
    # All BGCs without predicted domains should no longer be in baseNames    
    if len(pfdFiles - pfdBases) > 0:
        sys.exit("Error! The following files did NOT have their domtable files processed: " + ", ".join(pfdFiles - pfdBases))

    if options.skip_ma:
        print(" Running with skip_ma parameter: Assuming that the domains folder has all the fasta files")
        print(" Only extracting BGC group from input file")
    else:
        print(" Adding sequences to corresponding domains file")
            
        for outputbase in baseNames:
            if verbose:
                print("   Processing: " + outputbase)

            pfdFile = os.path.join(pfd_folder, outputbase + ".pfd")
            filtered_matrix = [map(lambda x: x.strip(), line.split('\t')) for line in open(pfdFile)]

            # save each domain sequence from a single BGC in its corresponding file
            fasta_file = os.path.join(bgc_fasta_folder, outputbase + ".fasta")
            
            # only create domain fasta if the pfd content is different from original and 
            #  domains folder has been emptied. Else, if trying to resume alignment phase,
            #  domain fasta files will contain duplicate sequence labels
            if not try_MA_resume:
                with open(fasta_file, "r") as fasta_file_handle:
                    fasta_dict = fasta_parser(fasta_file_handle) # all fasta info from a BGC
                save_domain_seqs(filtered_matrix, fasta_dict, domains_folder, outputbase)

            BGCs[outputbase] = BGC_dic_gen(filtered_matrix)

    # Get the ordered list of domains
    print(" Reading the ordered list of domains from the pfs files")
    for outputbase in baseNames:
        pfsfile = os.path.join(pfs_folder, outputbase + ".pfs")
        if os.path.isfile(pfsfile):
            DomainList[outputbase] = get_domain_list(pfsfile)
        else:
            sys.exit(" Error: could not open " + outputbase + ".pfs")

    #Write or retrieve BGC dictionary
    if not options.skip_all:
        if options.skip_hmmscan or options.skip_ma:
            with open(os.path.join(output_folder, "BGCs.dict"), "r") as BGC_file:
                BGCs = pickle.load(BGC_file)
                BGC_file.close()
        else:
            with open(os.path.join(output_folder, "BGCs.dict"), "w") as BGC_file:
                pickle.dump(BGCs, BGC_file)
                BGC_file.close()
    
    
    print("\n\n   - - Calculating distance matrix - -")
   
    # Do multiple alignments if needed
    if not options.skip_ma:
        print("Performing multiple alignment of domain sequences")
        
        # obtain all fasta files with domain sequences
        fasta_domains = set(glob(os.path.join(domains_folder,"*.fasta")))
        
        # compare with .algn set of files. Maybe resuming is possible if
        # no new sequences were added
        if try_MA_resume:
            temp_aligned = set(glob(os.path.join(domains_folder, "*.algn")))
            
            if len(temp_aligned) > 0:
                print(" Found domain fasta files without corresponding alignments")
                
                for a in temp_aligned:
                    if os.path.getsize(a) > 0:
                        fasta_domains.remove(a[:-5]+".fasta")
            
            temp_aligned.clear()
        
        # Try to further reduce the set of domain fastas that need alignment
        sequence_tag_list = set()
        header_list = []
        fasta_domains_temp = fasta_domains.copy()
        for domain_file in fasta_domains_temp:
            domain_name = domain_file.split(os.sep)[-1].replace(".fasta", "")
            
            # fill fasta_dict...
            with open(domain_file, "r") as fasta_handle:
                header_list = get_fasta_keys(fasta_handle)
                
            # Get the BGC name from the sequence tag. The form of the tag is:
            # >BGCXXXXXXX_BGCXXXXXXX_ORF25:gid...
            sequence_tag_list = set(s.split("_ORF")[0] for s in header_list)

            # ...to find out how many sequences do we actually have
            if len(sequence_tag_list) == 1:
                # avoid multiple alignment if the domains all belong to the same BGC
                fasta_domains.remove(domain_file)
                if verbose:
                    print(" Skipping Multiple Alignment for " + domain_name + " (appears only in one BGC)")
        
        sequence_tag_list.clear()
        del header_list[:]
        
        fasta_domains_temp.clear()
            
        # Do the multiple alignment
        if len(fasta_domains) > 0:
            if options.use_hmmalign:
                print("\n Using hmmalign")
                launch_hmmalign(cores, fasta_domains)
                                        
            else:
                print("\n Using MAFFT")
                for domain in fasta_domains:
                    domain_name = domain[:-6]
                    # Multiple alignment of all domain sequences
                    run_mafft(options.al_method, options.maxit, options.mafft_threads, options.mafft_pars, domain_name)
    
            # verify all tasks were completed by checking existance of alignment files
            for domain in fasta_domains:
                if not os.path.isfile(domain[:-6]+".algn"):
                    print("   WARNING, " + domain[:-6] + ".algn could not be found (possible issue with aligner).")
                       
        else:
            print(" No domain fasta files found to align")
    
    
    # If there's something to analyze, load the aligned sequences
    if options_samples or options_all:
        print(" Trying to read domain alignments (*.algn files)")
        aligned_files_list = glob(os.path.join(domains_folder, "*.algn"))
        if len(aligned_files_list) == 0:
            sys.exit("No aligned sequences found in the domain folder (run without the --skip_ma parameter or point to the correct output folder)")
        for aligned_file in aligned_files_list:
            with open(aligned_file, "r") as aligned_file_handle:
                fasta_dict = fasta_parser(aligned_file_handle)
                for header in fasta_dict:
                    AlignedDomainSequences[header] = fasta_dict[header]
    
    network_matrix_complete = {}
    # Try to make default analysis using all files found inside the input folder
    if options_all:
        print("\nGenerating distance network files with ALL available input files")
    
        # create output directory
        create_directory(os.path.join(output_folder, networks_folder_all), "Networks_all", False)
    
        # Making network files mixing all classes
        if options_mix:
            print("\n Mixing all BGC classes")
            
            print("  Calculating all pairwise distances")
            pairs = set(map(tuple, map(sorted, combinations(clusters, 2))))
            cluster_pairs = [(x, y, "mix") for (x, y) in pairs]
            network_matrix_mix = generate_network(cluster_pairs, cores)
                
            print("  Writing output files")
            for cutoff in cutoff_list:
                path = os.path.join(output_folder, networks_folder_all, "all_mix_c" + str(cutoff) + ".network")
                write_network_matrix(network_matrix_mix, cutoff, path, include_disc_nodes, group_dct)
                
            # free memory if we're not going to reuse this for samples
            if not options_samples:
                network_matrix_mix.clear()
        
        # Making network files separating by BGC class
        if options_classify:
            print("\n Working for each BGC class")
            
            # make sure the bgc lists are empty
            for bgc_class in BGC_classes:
                del BGC_classes[bgc_class][:]
        
            # Preparing gene cluster classes
            print("  Sorting the input BGCs\n")
            for cluster in clusters:
                product = group_dct[cluster][0]
                predicted_class = sort_bgc(product)
                if predicted_class in valid_classes:
                    BGC_classes[predicted_class].append(cluster)
                
                # possibly add hybrids to 'pure' classes
                if options.hybrids and predicted_class == "PKS-NRP_Hybrids":
                    if "nrps" in valid_classes:
                        BGC_classes["NRPS"].append(cluster)
                    if "t1pks" in product and "pksi" in valid_classes:
                        BGC_classes["PKSI"].append(cluster)
                    if "t1pks" not in product and "pksother" in valid_classes:
                        BGC_classes["PKSother"].append(cluster)

            for bgc_class in BGC_classes:
                if len(BGC_classes[bgc_class]) > 1:
                    print("\n  " + bgc_class + " (" + str(len(BGC_classes[bgc_class])) + " BGCs)")
                    # create output directory
                    create_directory(os.path.join(output_folder, networks_folder_all, bgc_class), "  All - " + bgc_class, False)
                            
                    print("   Calculating all pairwise distances")
                    pairs = set(map(tuple, map(sorted, combinations(BGC_classes[bgc_class], 2))))
                    cluster_pairs = [(x, y, bgc_class) for (x, y) in pairs]
                    network_matrix = generate_network(cluster_pairs, cores)
                        
                    print("   Writing output files")
                    for cutoff in cutoff_list:
                        path = os.path.join(output_folder, networks_folder_all, bgc_class, "all_" + bgc_class + "_c" + str(cutoff) + ".network")
                        write_network_matrix(network_matrix, cutoff, path, include_disc_nodes, group_dct)
                        
                    # keep the data if we have to reuse it
                    if options_samples:
                        network_matrix_complete.update(network_matrix)

    # Try to make analysis for each sample
    if options_samples:
        network_matrix_sample = {}
        
        if len(sampleDict) == 1 and options_all:
            print("\nNOT generating networks per sample (only one sample, covered in the all-vs-all case)")
        else:
            print("\nGenerating distance network files for each sample")
            
            # create output directory for all samples
            create_directory(os.path.join(output_folder, networks_folder_samples), "Samples", False)
            
            for sample, sampleClusters in sampleDict.iteritems():
                print("\n Sample: " + sample)
                if len(sampleClusters) == 1:
                    print(" Warning: Sample size = 1 detected. Not generating network for this sample (" + sample + ")")
                else:
                    # create output directory for this sample
                    create_directory(os.path.join(output_folder, networks_folder_samples, sample), " Samples - " + sample, False)
                        
                    # Making network files mixing all classes
                    if options_mix:
                        print("\n  Mixing all BGC classes")
                            
                        pairs = set(map(tuple, map(sorted, combinations(sampleClusters, 2))))
                        
                        # If we did the 'all' case and didn't mix 'classify' and 'mix', 
                        # the pairs' distances should be ready
                        if options_all and options_mix:
                            print("   Using distances calculated in the 'all' analysis")
                            for pair in pairs:
                                network_matrix_sample[pair[0], pair[1], "mix"] = network_matrix_mix[pair[0], pair[1], "mix"]
                            network_matrix_mix.clear()
                        else:
                            print("   Calculating all pairwise distances")
                            cluster_pairs = [(x, y, "mix") for (x, y) in pairs]
                            network_matrix_sample = generate_network(cluster_pairs, cores)

                        print("   Writing output files")
                        for cutoff in cutoff_list:
                            path = os.path.join(output_folder, networks_folder_samples, "sample_" + sample + "_mix_c" + str(cutoff) + ".network")
                            write_network_matrix(network_matrix_sample, cutoff, path, include_disc_nodes, group_dct)
                    
                    # Making network files separating by BGC class
                    if options_classify:
                        print("\n  Working for each BGC class")
                        
                        # make sure the bgc lists are empty
                        for bgc_class in BGC_classes:
                            del BGC_classes[bgc_class][:]
                    
                        # Preparing gene cluster classes
                        print("   Sorting the input BGCs\n")
                        for cluster in sampleClusters:
                            product = group_dct[cluster][0]
                            predicted_class = sort_bgc(product)
                            if predicted_class in valid_classes:
                                BGC_classes[predicted_class].append(cluster)
                            
                            # possibly add hybrids to 'pure' classes
                            if options.hybrids and predicted_class == "PKS-NRP_Hybrids":
                                if "nrps" in valid_classes:
                                    BGC_classes["NRPS"].append(cluster)
                                if "t1pks" in product and "pksi" in valid_classes:
                                    BGC_classes["PKSI"].append(cluster)
                                if "t1pks" not in product and "pksother" in valid_classes:
                                    BGC_classes["PKSother"].append(cluster)
                        
                        for bgc_class in BGC_classes:
                            if len(BGC_classes[bgc_class]) > 1:
                                print("\n   " + bgc_class + " (" + str(len(BGC_classes[bgc_class])) + " BGCs)")
                                network_matrix_sample.clear()

                                if len(BGC_classes[bgc_class]) > 1:
                                    # create output directory
                                    create_directory(os.path.join(output_folder, networks_folder_samples, sample, bgc_class), "   Sample " + sample + " - " + bgc_class, False)
                                                    
                                    pairs = set(map(tuple, map(sorted, combinations(BGC_classes[bgc_class], 2))))
                                    
                                    if options_all and options_classify:
                                        print("    Using distances calculated in the 'all' analysis")
                                        for pair in pairs:
                                            network_matrix_sample[pair[0], pair[1], bgc_class] = network_matrix_complete[pair[0], pair[1], bgc_class]
                                    else:
                                        print("    Calculating all pairwise distances")
                                        cluster_pairs = [(x, y, bgc_class) for (x, y) in pairs]
                                        network_matrix_sample = generate_network(cluster_pairs, cores)
                                        
                                    print("    Writing output files")
                                    for cutoff in cutoff_list:
                                        path = os.path.join(output_folder, networks_folder_samples, sample, bgc_class, "sample_"+sample+"_"+bgc_class+"_c" + str(cutoff) + ".network")
                                        write_network_matrix(network_matrix_sample, cutoff, path, include_disc_nodes, group_dct)


    runtime = time.time()-time1
    runtime_string = '\n\n\tMain function took %0.3f s' % (runtime)
    timings_file.write(runtime_string + "\n")
    print runtime_string
    
    timings_file.close()
