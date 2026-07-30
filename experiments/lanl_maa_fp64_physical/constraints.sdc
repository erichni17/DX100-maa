set clk_period 10.0

create_clock -name core_clock -period $clk_period [get_ports clock]
set_input_delay 0.25 -clock core_clock [all_inputs -no_clocks]
set_output_delay 0.25 -clock core_clock [all_outputs]
set_false_path -from [get_ports nReset]
