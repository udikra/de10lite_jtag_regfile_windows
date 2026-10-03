module jtag_regfile(output wire [9:0] LEDR);
    wire jtag_tck;
    wire jtag_tdi;
    wire jtag_tdo;
    wire jtag_state_tlr;
    wire [0:0] virtual_ir_in;
    wire virtual_state_cdr;
    wire virtual_state_sdr;
    wire virtual_state_udr;
    wire [31:0] reg_data_out;
    wire reg_rst_n = ~jtag_state_tlr;

    reg [39:0] dr_shift;

    sld_virtual_jtag #(
        .sld_auto_instance_index("YES"),
        .sld_instance_index(0),
        .sld_ir_width(1)
    ) virtual_jtag (
        .tck(jtag_tck),
        .tdi(jtag_tdi),
        .tdo(jtag_tdo),
        .ir_out(1'b0),
        .ir_in(virtual_ir_in),
        .virtual_state_cdr(virtual_state_cdr),
        .virtual_state_sdr(virtual_state_sdr),
        .virtual_state_udr(virtual_state_udr),
        .jtag_state_tlr(jtag_state_tlr)
    );

    reg_file registers (
        .clk(jtag_tck),
        .rst_n(reg_rst_n),
        .enable(virtual_state_udr && virtual_ir_in == 1'b0),
        .addr(dr_shift[3:0]),
        .wr(dr_shift[4]),
        .data_in(dr_shift[36:5]),
        .data_out(reg_data_out)
    );

    always @(posedge jtag_tck or posedge jtag_state_tlr) begin
        if (jtag_state_tlr) begin
            dr_shift <= 40'b0;
        end else if (virtual_ir_in == 1'b0 && virtual_state_cdr) begin
            dr_shift <= {3'b0, reg_data_out, 1'b0, 4'b0};
        end else if (virtual_ir_in == 1'b0 && virtual_state_sdr) begin
            dr_shift <= {jtag_tdi, dr_shift[39:1]};
        end
    end

    assign jtag_tdo = dr_shift[0];
    assign LEDR = reg_data_out[9:0];
endmodule