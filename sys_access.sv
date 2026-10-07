// sys_access.sv  -  JTAG-driven master on the system address space (sys_clk domain)
//
// Turns commands written over JTAG into transactions on the sa_* bus, either
// one word (address and data in registers) or a block of words moved between
// the JTAG buffer and consecutive system addresses.
//
// Registers (JWSR = written over JTAG, SWJR = read over JTAG):
//   JWSR 5  SA_ADDR    byte address. Single access: driven on sa_addr as is.
//                      Block: bits [1:0] are ignored, the block starts at
//                      the aligned word.
//   JWSR 6  SA_DATA    single write: the write data.
//                      block: word count, 1..BUF_WORDS.
//   JWSR 7  SA_CMD     every JTAG write starts a command:
//                        [2:0]   op: 0 abort, 1 read, 2 write,
//                                    3 block write (buffer -> system),
//                                    4 block read  (system -> buffer)
//                        [15:8]  tag, echoed in SA_STATUS when done
//                        [19:16] block write: byte mask of the first word
//                        [23:20] block write: byte mask of the last word
//   SWJR 14 SA_RDATA   single read: the data returned on sa_data_in
//   SWJR 15 SA_STATUS  [0] busy, [1] the last command was aborted,
//                      [15:8] tag of the last finished command,
//                      [31:16] BUF_WORDS (identifies this engine)
//
// A command written while busy is ignored, except abort, which drops sa_enable
// and returns to idle at once (for a system that never raises sa_ready).
//
// Block word k sits in buffer word k and at system address start + 4 * k.
// A block write merges a word whose byte mask is not 4'hF into the current
// system word (read-modify-write); mask bit n selects bits 8n+7..8n, which is
// system byte address word + n. A one-word block uses both masks ANDed.
//
// sa_* bus: the engine holds sa_enable, sa_wr, sa_addr and sa_data_out stable
// until a cycle with sa_ready high, which completes the transaction (read data
// is taken from sa_data_in in that cycle). sa_ready is ignored while sa_enable
// is low, and a new transaction may start in the next cycle.
//
// The engine is the only user of the buffer's system port, and touches it only
// while busy. The host must not scan the buffer until SA_STATUS shows the
// command finished.
module sys_access #(
    parameter int BUF_ADDR_BITS = 8
) (
    input  wire               clk,
    input  wire               rst_n,

    input  wire  [31:0]       reg_addr,         // JWSR 5
    input  wire  [31:0]       reg_data,         // JWSR 6
    input  wire  [31:0]       reg_cmd,          // JWSR 7
    input  wire               reg_cmd_wr,       // JTAG wrote JWSR 7
    output logic [31:0]       reg_rdata,        // SWJR 14
    output wire  [31:0]       reg_status,       // SWJR 15

    output logic              buf_en,
    output logic              buf_rd,
    output logic [BUF_ADDR_BITS-1:0] buf_addr,
    output logic [31:0]       buf_wdata,
    input  wire  [31:0]       buf_rdata,

    output logic [31:0]       sa_addr,
    output logic              sa_enable,
    output logic              sa_wr,
    output logic [31:0]       sa_data_out,
    input  wire  [31:0]       sa_data_in,
    input  wire               sa_ready
);
    localparam int BUF_WORDS = 1 << BUF_ADDR_BITS;

    localparam logic [2:0] OP_ABORT     = 3'd0;
    localparam logic [2:0] OP_READ      = 3'd1;
    localparam logic [2:0] OP_WRITE     = 3'd2;
    localparam logic [2:0] OP_BLK_WRITE = 3'd3;
    localparam logic [2:0] OP_BLK_READ  = 3'd4;

    typedef enum logic [2:0] {
        IDLE,           // waiting for a command
        SINGLE,         // single read or write on the bus
        BW_FETCH,       // block write: buffer read address presented
        BW_DATA,        // block write: buffer word on buf_rdata
        BW_MERGE_RD,    // block write: reading the system word to merge into
        BW_WRITE,       // block write: writing the word to the system
        BR_READ         // block read: reading a system word into the buffer
    } state_t;

    state_t                   state;
    logic                     aborted;
    logic [7:0]               tag;              // of the command in flight
    logic [7:0]               done_tag;         // of the last finished command
    logic [BUF_ADDR_BITS-1:0] idx;              // block word index
    logic [BUF_ADDR_BITS-1:0] last;             // index of the last block word
    logic [3:0]               first_mask;
    logic [3:0]               last_mask;

    wire [2:0] cmd_op = reg_cmd[2:0];
    wire       sa_done = sa_enable && sa_ready;
    wire       last_word = idx == last;
    wire [3:0] word_mask = (idx == '0 ? first_mask : 4'hF)
                         & (last_word ? last_mask : 4'hF);

    // Bytes selected by mask from new_data, the rest from keep.
    function automatic logic [31:0] merge(input logic [31:0] keep,
                                          input logic [31:0] new_data,
                                          input logic [3:0]  mask);
        for (int n = 0; n < 4; n++)
            merge[8*n +: 8] = mask[n] ? new_data[8*n +: 8] : keep[8*n +: 8];
    endfunction

    assign reg_status = {16'(BUF_WORDS), done_tag, 6'b0, aborted, state != IDLE};

    always @(posedge clk) begin
        buf_en <= 1'b0;
        if (!rst_n) begin
            state <= IDLE;
            aborted <= 1'b0;
            tag <= 8'd0;
            done_tag <= 8'd0;
            reg_rdata <= 32'h0;
            sa_enable <= 1'b0;
            sa_wr <= 1'b0;
            sa_addr <= 32'h0;
            sa_data_out <= 32'h0;
            buf_rd <= 1'b1;
            buf_addr <= '0;
            buf_wdata <= 32'h0;
        end else if (reg_cmd_wr && cmd_op == OP_ABORT) begin
            state <= IDLE;
            aborted <= 1'b1;
            sa_enable <= 1'b0;
            done_tag <= reg_cmd[15:8];
        end else begin
            case (state)
            IDLE: if (reg_cmd_wr) begin
                aborted <= 1'b0;
                tag <= reg_cmd[15:8];
                first_mask <= reg_cmd[19:16];
                last_mask <= reg_cmd[23:20];
                last <= BUF_ADDR_BITS'(reg_data - 32'd1);
                idx <= '0;
                case (cmd_op)
                OP_READ, OP_WRITE: begin
                    state <= SINGLE;
                    sa_addr <= reg_addr;
                    sa_wr <= cmd_op == OP_WRITE;
                    sa_data_out <= reg_data;
                    sa_enable <= 1'b1;
                end
                OP_BLK_WRITE: begin
                    state <= BW_FETCH;
                    sa_addr <= {reg_addr[31:2], 2'b00};
                    buf_en <= 1'b1;
                    buf_rd <= 1'b1;
                    buf_addr <= '0;
                end
                OP_BLK_READ: begin
                    state <= BR_READ;
                    sa_addr <= {reg_addr[31:2], 2'b00};
                    sa_wr <= 1'b0;
                    sa_enable <= 1'b1;
                end
                default: done_tag <= reg_cmd[15:8];   // unknown op: nothing to do
                endcase
            end

            SINGLE: if (sa_done) begin
                if (!sa_wr)
                    reg_rdata <= sa_data_in;
                state <= IDLE;
                sa_enable <= 1'b0;
                done_tag <= tag;
            end

            // The RAM registers the address at this edge; data follows.
            BW_FETCH: state <= BW_DATA;

            BW_DATA: begin
                sa_data_out <= buf_rdata;
                sa_enable <= 1'b1;
                if (word_mask == 4'hF) begin
                    state <= BW_WRITE;
                    sa_wr <= 1'b1;
                end else begin
                    state <= BW_MERGE_RD;
                    sa_wr <= 1'b0;
                end
            end

            BW_MERGE_RD: if (sa_done) begin
                state <= BW_WRITE;
                sa_wr <= 1'b1;
                sa_data_out <= merge(sa_data_in, sa_data_out, word_mask);
            end

            BW_WRITE: if (sa_done) begin
                sa_enable <= 1'b0;
                if (last_word) begin
                    state <= IDLE;
                    done_tag <= tag;
                end else begin
                    state <= BW_FETCH;
                    idx <= idx + 1'b1;
                    sa_addr <= sa_addr + 32'd4;
                    buf_en <= 1'b1;
                    buf_rd <= 1'b1;
                    buf_addr <= idx + 1'b1;
                end
            end

            // Each word read goes into the buffer, and the next read starts
            // in the same cycle.
            BR_READ: if (sa_done) begin
                buf_en <= 1'b1;
                buf_rd <= 1'b0;
                buf_addr <= idx;
                buf_wdata <= sa_data_in;
                if (last_word) begin
                    state <= IDLE;
                    sa_enable <= 1'b0;
                    done_tag <= tag;
                end else begin
                    idx <= idx + 1'b1;
                    sa_addr <= sa_addr + 32'd4;
                end
            end

            default: state <= IDLE;
            endcase
        end
    end
endmodule
