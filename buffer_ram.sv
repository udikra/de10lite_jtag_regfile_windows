// buffer_ram.sv  -  256 x 32-bit true dual-port RAM, one clock per port
//
// Direct altsyncram instance (M9K blocks, true dual-port, two clocks). Reads
// have one cycle of latency: address and control are registered, the output
// is not. While en_x is low the port neither reads nor writes, and q_x holds.
// A same-address access from both ports at once is undefined, so the two
// clock domains must take turns (see jtag_bridge.sv).
module buffer_ram #(
    parameter int ADDR_BITS = 8,
    parameter int DATA_BITS = 32
) (
    input  wire                 clk_a,
    input  wire                 en_a,
    input  wire                 we_a,
    input  wire [ADDR_BITS-1:0] addr_a,
    input  wire [DATA_BITS-1:0] d_a,
    output wire [DATA_BITS-1:0] q_a,

    input  wire                 clk_b,
    input  wire                 en_b,
    input  wire                 we_b,
    input  wire [ADDR_BITS-1:0] addr_b,
    input  wire [DATA_BITS-1:0] d_b,
    output wire [DATA_BITS-1:0] q_b
);
    altsyncram #(
        .operation_mode("BIDIR_DUAL_PORT"),
        .intended_device_family("MAX 10"),
        .ram_block_type("M9K"),
        .width_a(DATA_BITS),
        .widthad_a(ADDR_BITS),
        .numwords_a(1 << ADDR_BITS),
        .width_b(DATA_BITS),
        .widthad_b(ADDR_BITS),
        .numwords_b(1 << ADDR_BITS),
        .width_byteena_a(1),
        .width_byteena_b(1),
        .address_reg_b("CLOCK1"),
        .indata_reg_b("CLOCK1"),
        .wrcontrol_wraddress_reg_b("CLOCK1"),
        .outdata_reg_a("UNREGISTERED"),
        .outdata_reg_b("UNREGISTERED"),
        .clock_enable_input_a("NORMAL"),
        .clock_enable_input_b("NORMAL"),
        .clock_enable_output_a("BYPASS"),
        .clock_enable_output_b("BYPASS"),
        .read_during_write_mode_port_a("NEW_DATA_NO_NBE_READ"),
        .read_during_write_mode_port_b("NEW_DATA_NO_NBE_READ"),
        .power_up_uninitialized("FALSE"),
        .lpm_type("altsyncram")
    ) ram (
        .clock0(clk_a),
        .clocken0(en_a),
        .wren_a(we_a),
        .address_a(addr_a),
        .data_a(d_a),
        .q_a(q_a),

        .clock1(clk_b),
        .clocken1(en_b),
        .wren_b(we_b),
        .address_b(addr_b),
        .data_b(d_b),
        .q_b(q_b),

        .aclr0(1'b0),
        .aclr1(1'b0),
        .addressstall_a(1'b0),
        .addressstall_b(1'b0),
        .byteena_a(1'b1),
        .byteena_b(1'b1),
        .clocken2(1'b1),
        .clocken3(1'b1),
        .rden_a(1'b1),
        .rden_b(1'b1),
        .eccstatus()
    );
endmodule
