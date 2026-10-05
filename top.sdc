create_clock -name MAX10_CLK1_50 -period 20.000 [get_ports MAX10_CLK1_50]
create_clock -name altera_reserved_tck -period 100.000 [get_ports altera_reserved_tck]

derive_pll_clocks
derive_clock_uncertainty

# JTAG and system clocks are unrelated; jtag_bridge.sv handles the crossing
# with a req/ack handshake, so every path between them is asynchronous.
set_clock_groups -asynchronous \
    -group [get_clocks altera_reserved_tck] \
    -group [get_clocks MAX10_CLK1_50]

set_false_path -from [get_ports {KEY[0]}]
set_false_path -to [get_ports {LEDR[*]}]
