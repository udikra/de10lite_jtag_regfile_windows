// jtag_bridge.sv  -  Virtual JTAG <-> sys_clk register bridge
//
// Register map (16 x 32-bit):
//   0..7   JWSR  JTAG writes, system reads. Stored in the sys_clk domain.
//   8..15  SWJR  System writes, JTAG reads. Sampled from the swjr input.
//
// DR frame (40 bits, LSB shifted first):
//   request : [3:0] addr, [4] wr, [36:5] data, [38:37] reserved, [39] nop
//   response: [36:5] rd_data, [37] done, [38] overrun, [39] 0
//
// TCK only runs during scans, so every command crosses into sys_clk with a
// req/ack toggle handshake: Update-DR latches the command into cmd_hold and
// toggles req; sys_clk executes it and returns ack = req. cmd_hold and rd_hold
// are only sampled after the toggle has passed a 2-FF synchronizer, so the
// multi-bit fields are stable when read. A command arriving while the previous
// one is still in flight is dropped and reported through the overrun flag.
module jtag_bridge (
    input  wire              sys_clk,
    input  wire              sys_rst_n,
    output logic [7:0][31:0] jwsr,
    output logic [7:0]       jwsr_wr,   // one sys_clk pulse per JTAG write
    input  wire  [7:0][31:0] swjr
);
    wire jtag_tck;
    wire jtag_tdi;
    wire jtag_tdo;
    wire jtag_state_tlr;
    wire [0:0] virtual_ir_in;
    wire virtual_state_cdr;
    wire virtual_state_sdr;
    wire virtual_state_udr;

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

    // Handshake toggles are never reset, so a reset on either side cannot
    // leave req and ack permanently unequal. They start at 0 on configuration.
    reg        req = 1'b0;              // TCK domain
    reg        ack = 1'b0;              // sys_clk domain
    reg [36:0] cmd_hold;                // TCK domain: {data, wr, addr}
    reg [31:0] rd_hold;                 // sys_clk domain

    // ---------------------------------------------------------------- TCK side
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] ack_sync = 2'b0;
    reg [39:0] dr_shift;
    reg        overrun;

    wire dr_selected = virtual_ir_in == 1'b0;
    wire busy = req != ack_sync[1];
    wire issue = dr_selected && virtual_state_udr && !dr_shift[39];

    always @(posedge jtag_tck)
        ack_sync <= {ack_sync[0], ack};

    always @(posedge jtag_tck) begin
        if (issue && !busy) begin
            cmd_hold <= dr_shift[36:0];
            req <= ~req;
        end
    end

    always @(posedge jtag_tck or posedge jtag_state_tlr) begin
        if (jtag_state_tlr) begin
            dr_shift <= 40'b0;
            overrun <= 1'b0;
        end else if (dr_selected && virtual_state_cdr) begin
            dr_shift <= {1'b0, overrun, ~busy, rd_hold, 5'b0};
            overrun <= 1'b0;
        end else if (dr_selected && virtual_state_sdr) begin
            dr_shift <= {jtag_tdi, dr_shift[39:1]};
        end else if (issue && busy) begin
            overrun <= 1'b1;
        end
    end

    assign jtag_tdo = dr_shift[0];

    // ------------------------------------------------------------ sys_clk side
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] req_sync = 2'b0;

    wire [3:0]  cmd_addr = cmd_hold[3:0];
    wire        cmd_wr   = cmd_hold[4];
    wire [31:0] cmd_data = cmd_hold[36:5];

    always @(posedge sys_clk)
        req_sync <= {req_sync[0], req};

    // While in reset, pending commands wait (the host sees done = 0) and run
    // after release. JTAG writes to SWJR addresses are ignored.
    always @(posedge sys_clk) begin
        jwsr_wr <= 8'b0;
        if (!sys_rst_n) begin
            jwsr <= '0;
            rd_hold <= 32'h0;
        end else if (req_sync[1] != ack) begin
            ack <= req_sync[1];
            if (cmd_wr) begin
                if (!cmd_addr[3]) begin
                    jwsr[cmd_addr[2:0]] <= cmd_data;
                    jwsr_wr[cmd_addr[2:0]] <= 1'b1;
                end
            end else begin
                rd_hold <= cmd_addr[3] ? swjr[cmd_addr[2:0]] : jwsr[cmd_addr[2:0]];
            end
        end
    end
endmodule
