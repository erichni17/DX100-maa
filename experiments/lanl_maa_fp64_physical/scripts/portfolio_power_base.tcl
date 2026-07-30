# LANL FP64 counterpart of bazel-orfs power_base.tcl.  The upstream helper
# hard-codes an active-high port named reset; this top exposes active-low
# nReset, so steady-state power analysis must hold nReset high.

source $::env(SCRIPTS_DIR)/util.tcl

if { [info exists ::env(TECH_LEF)] } {
    read_lef $::env(TECH_LEF)
    foreach lef $::env(SC_LEF) {
        read_lef $lef
    }
    if { [info exists ::env(ADDITIONAL_LEFS)] } {
        foreach lef $::env(ADDITIONAL_LEFS) {
            read_lef $lef
        }
    }
}

foreach libFile $::env(LIB_FILES) {
    log_cmd read_liberty $libFile
}

foreach file $::env(SPEFS_AND_NETLISTS) {
    if { [string match *.v $file] } {
        log_cmd read_verilog $file
    }
}

log_cmd link_design $::env(DESIGN_NAME)
log_cmd read_sdc $::env(RESULTS_DIR)/$::env(POWER_STAGE).sdc

foreach file $::env(SPEFS_AND_NETLISTS) {
    if { [string match *.spef $file] } {
        log_cmd read_spef $file
    }
}

report_parasitic_annotation
report_units

set_case_analysis 1 nReset
