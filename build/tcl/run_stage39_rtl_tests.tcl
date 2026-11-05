# Stage39 cold-reproduction RTL tests. The Python preparation step supplies
# runtime-load packets and generated testbenches in the directory argument.
if {[llength $argv] != 1} {error "usage: vivado -mode batch -source run_stage39_rtl_tests.tcl -tclargs <sim-work>"}
set work [file normalize [lindex $argv 0]]
if {![file isdirectory $work]} {error "simulation work directory missing: $work"}
cd $work
set rtl [glob -nocomplain [file join $work rtl *.v] [file join $work rtl *.sv]]
if {[llength $rtl] != 9} {error "expected nine RTL files, found [llength $rtl]"}

proc run_test {work rtl top snapshot pass_token log_name} {
  catch {file delete -force [file join $work xsim.dir]}
  foreach name {xvlog.log xelab.log xsim.log} {catch {file delete -force [file join $work $name]}}
  set tb [file join $work ${top}.sv]
  if {![file exists $tb]} {error "testbench missing: $tb"}
  if {[catch {exec xvlog -sv {*}$rtl $tb 2>@1} compile]} {puts $compile; error "xvlog failed for $top"}
  puts $compile
  if {[catch {exec xelab $top -s $snapshot 2>@1} elaborate]} {puts $elaborate; error "xelab failed for $top"}
  puts $elaborate
  if {[catch {exec xsim $snapshot -R 2>@1} simulate]} {puts $simulate; error "xsim failed for $top"}
  puts $simulate
  set handle [open [file join $work $log_name] w]; puts $handle $simulate; close $handle
  if {[string first $pass_token $simulate] < 0} {error "PASS token missing for $top"}
}

run_test $work $rtl tb_stage39_universal_generated stage39_universal_sim \
  "STAGE39_RUNTIME_LOAD_CACHE_AND_INFERENCE_PASS" universal.log
run_test $work $rtl tb_stage39_multiscene_generated stage39_multiscene_sim \
  "STAGE39_FOUR_SCENE_SWITCH_AND_RETURN_PASS" multiscene.log
puts "STAGE39 RTL TESTS PASS"

